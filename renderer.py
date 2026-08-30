"""GIF 渲染管线（两幕动画版 v0.2，独立可测，仅依赖标准库 + Pillow + 本插件 config）。

设计文档 10.6 要求：
- 可脱离 QQ/AstrBot 单独运行测试
- 输入：玩家列表、回答者索引、提问者序号、动画参数（颜色/时长/标签）
- 输出：GIF 字节（不写 NETSCAPE 扩展 = QQ 播一遍停）
- 静态帧与动画帧共用同一套绘制逻辑

两幕结构（桌面测试版 v6 验收成果移植，2026-08-29）：
  第一幕：竖向滚轮三段运动（快速→刹车→长缓出，速度连续无卡顿）
          → 定格选中者（金框金字）→「回答者」垫字逐渐显现
  第二幕：其他格子淡出 → 选中格上移 1.5 格（垫字跟随，不再渐显）
          → 纯数字横转盘从右侧滑入 → 滚动减速 → 金属红框定住最终数字
          （「提问者」按时间进度 1/3 起从左到右逐字渐显，停下时全显）

菱形剖面：行高随离中心距离连续变化（中心 LENS_SCALE 倍 → 邻格收缩 →
屏幕外恢复 1），模拟 3D 滚轮前表面；4.5 格视口总量不变，字号随格高等比缩放。

硬裁剪：每行单独成层贴入视口，越界部分几何裁剪（不是靠上下栏遮挡），
上下栏只承担信息展示，栏透明/背景图均不影响裁剪行为。
"""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config as CFG

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """字体解析链：系统字体候选 → PIL 默认。找不到必须降级不崩溃。"""
    if size in _font_cache:
        return _font_cache[size]
    font = None
    for p in CFG.FONT_CANDIDATES:
        if Path(p).is_file():
            try:
                font = ImageFont.truetype(p, size)
                break
            except OSError:
                continue
    if font is None:
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def _sanitize_name(raw: str) -> str:
    """去掉字体无法渲染的字符（如 emoji 等非 BMP 字符，微软雅黑无字形会画成方框）。

    仅保留基本平面字符；整名被清空时回退「玩家」。
    """
    s = "".join(c for c in raw if ord(c) <= 0xFFFF)
    return s or "玩家"


def _first_char(name: str) -> str:
    """昵称首字（清洗后；全空回退「玩家」的首字「玩」）。"""
    return _sanitize_name(name)[0]


def _fit_font(text: str, max_w: int) -> int:
    """从 96（第一幕昵称字号）起步，超宽向下适配（下限 24）。"""
    size = 26
    while size > 6 and load_font(size).getlength(text) > max_w:
        size -= 2
    return size


def _clip_name(raw: str, sc: float = 1.0) -> str:
    """按像素宽度截断显示名（设计文档 10.7，不做简单 [:N] 截断）。"""
    raw = _sanitize_name(raw)
    if len(raw) <= CFG.NAME_MAX_CHARS:
        return raw
    font = load_font(int(13 * sc))
    limit = int(CFG.CANVAS_W * sc) - int(13 * sc)
    if font.getlength(raw) <= limit:
        return raw
    ell = "…"
    while raw and font.getlength(raw + ell) > limit:
        raw = raw[:-1]
    return raw + ell


# ---------- 菱形剖面（3D 透镜，测试版 v6 验收曲线） ----------

def _shape_h(d: float, s: float) -> float:
    """行高系数（单位行高）。d=|行号-中心行号|。

    剖面：中心 s；邻格 1-0.2(s-1)；次邻格 1-0.55(s-1)（肉眼可见的阶梯）；
    d=3 恢复 1（屏幕外，滚动经过中心时平滑鼓胀）。s=1 退化均匀行高。
    """
    d = abs(d)
    k = s - 1
    if d <= 1:
        sh = 1 - 1.2 * d
    elif d <= 2:
        sh = -0.2 - 0.35 * (d - 1)
    elif d <= 3:
        sh = -0.55 + 0.55 * (d - 2)
    else:
        sh = 0.0
    return 1 + k * sh


def _shape_int(d: float) -> float:
    """∫shape 偏移（0..d，d≥0；d>3 恒为 -0.25）。"""
    if d <= 1:
        return d - 0.6 * d * d
    if d <= 2:
        return 0.4 - 0.2 * (d - 1) - 0.175 * (d - 1) ** 2
    if d <= 3:
        dd = d - 2
        return 0.025 - 0.55 * dd + 0.275 * dd * dd
    return -0.25


def _lens_y(d: float, s: float) -> float:
    """离中心 d 格处的行中心偏移（单位行高）。d + (s-1)·∫shape，奇对称。"""
    if d < 0:
        return -_lens_y(-d, s)
    return d + (s - 1) * _shape_int(d)


def _font_for(d: float, s: float) -> int:
    """字号 = 基准渐变字号 × 菱形行高系数（整个格子等比缩放）。s>1 下限 18。"""
    base = max(8, int(13 - abs(d) * 2))
    if s <= 1.001:
        return base
    return max(5, int(base * _shape_h(d, s) + 0.5))


# ---------- 布局辅助 ----------

def _move_to(s: float, row_h: float, view_top: float, center_y: float,
             sc: float = 1.0) -> float:
    """第二幕金框行上移目标：约 1.5 格，保证高格不顶标题栏。"""
    return max(center_y - 1.5 * row_h,
               view_top + max(1, int(4 * sc)) + s * row_h / 2)


def _wheel_y0(s: float, row_h: float, view_bottom: float, move_to: float,
              sc: float = 1.0) -> int:
    """横转条 y0：金框行下缘与下栏中点，再下移（条底边与下栏空隙）的一半。"""
    mid = int((move_to + s * row_h / 2 + view_bottom) / 2 - int(14 * sc))
    return mid + (view_bottom - (mid + int(28 * sc))) // 2


# ---------- 单元素绘制 ----------

def _draw_base(colors: dict, footer_text: str, title: str | None = None) -> Image.Image:
    """底图：背景图（预留入口，当前恒 None）或纯色 + 上下栏（可透明模式）。"""
    sc = colors.get("res_scale", 1.0)
    cw, ch = int(CFG.CANVAS_W * sc), int(CFG.CANVAS_H * sc)
    fs = lambda s_: load_font(int(s_ * sc))
    lw = max(1, int(1 * sc))
    view_top = int(CFG.TITLE_H * sc)
    view_bottom = ch - int(CFG.FOOTER_H * sc)
    bg = colors.get("background")
    if bg is not None:
        img = bg.convert("RGB").resize((cw, ch))
    else:
        img = Image.new("RGB", (cw, ch), colors["bg"])
    d = ImageDraw.Draw(img)
    if bool(colors.get("bar_opaque", True)):
        d.rectangle([0, 0, cw, view_top], fill=colors["title_bg"])
        d.line([0, view_top - 1, cw, view_top - 1], fill=colors["line"], width=lw)
        d.rectangle([0, view_bottom, cw, ch], fill=colors["title_bg"])
        d.line([0, view_bottom, cw, view_bottom], fill=colors["line"], width=lw)
    # 标题同样剥离字体画不出的字符（emoji → 方框），全空回退默认
    title = "".join(c for c in (title or CFG.TITLE_DEFAULT) if ord(c) <= 0xFFFF) or CFG.TITLE_DEFAULT
    d.text((cw / 2, view_top / 2), title,
           fill=colors["title_text"], font=fs(7), anchor="mm")
    d.text((cw / 2, view_bottom + int(CFG.FOOTER_H * sc) / 2), footer_text,
           fill=colors["title_text"], font=fs(6), anchor="mm")
    return img


def _draw_row(img: Image.Image, names: list[str], cy: float, r: int, size: int,
              row_h: float, colors: dict, alpha: float = 1.0, selected: bool = False,
              gold: bool = False, label: str | None = None, label_size: int | None = None,
              label_color: tuple | None = None, label_alpha: float = 0.0,
              label_dy: float = 0.0) -> None:
    """在 cy 画一行（行高 row_h，超出视口部分几何硬裁剪）。r=行号（斑马纹/名字取模）。

    图层序：底色 < label（回答者垫字）< 名字 < 金框。
    淡出用向 bg 纯色混合的伪透明（背景图功能上线前有效；届时改真 alpha）。
    """
    sc = colors.get("res_scale", 1.0)
    cw = int(CFG.CANVAS_W * sc)
    fs = lambda s_: load_font(int(s_ * sc))
    lw = max(1, int(1 * sc))
    view_top = int(CFG.TITLE_H * sc)
    view_bottom = int(CFG.CANVAS_H * sc) - int(CFG.FOOTER_H * sc)
    top = cy - row_h / 2
    layer = Image.new("RGB", (cw, int(row_h) + int(2 * sc)))
    ld = ImageDraw.Draw(layer)
    zebra = colors["zebra_a"] if r % 2 == 0 else colors["zebra_b"]
    ld.rectangle([0, 0, cw, layer.height], fill=zebra)
    ld.line([0, 0, cw, 0], fill=colors["line"], width=lw)
    if label and label_alpha > 0:
        label_alpha = min(label_alpha, CFG.LABEL_ALPHA_MAX)   # 封顶 50%：只留影子感
        lc = tuple(int(zebra[i] * (1 - label_alpha) + label_color[i] * label_alpha)
                   for i in range(3))
        ld.text((cw / 2, layer.height / 2 + label_dy), label, fill=lc,
                font=fs(label_size), anchor="mm")
    name = _clip_name(names[r % len(names)], sc)
    if gold:
        color = colors["selected"]["gold"]
        stroke = colors["selected"]["stroke"]
    else:
        color = colors["text"]
        stroke = 0
    ld.text((cw / 2, layer.height / 2), name, fill=color, font=fs(size),
            anchor="mm", stroke_width=stroke,
            stroke_fill=colors["selected"]["gold"] if stroke else None)
    if selected:
        ld.rectangle([0, int(2 * sc), cw - 1, layer.height - int(3 * sc)],
                     outline=colors["selected"]["gold"],
                     width=colors["selected"]["box_width"])
    if alpha < 1.0:
        layer = Image.blend(Image.new("RGB", layer.size, colors["bg"]), layer, alpha)
    y_top, y_bot = int(top), int(top + layer.height)
    if y_bot <= view_top or y_top >= view_bottom:
        return
    src_y0 = max(0, view_top - y_top)
    src_y1 = min(layer.height, view_bottom - y_top)
    img.paste(layer.crop((0, src_y0, cw, src_y1)), (0, max(y_top, view_top)))


def _draw_hwheel_layer(names: list[str], x_offset: float, y0: int, colors: dict,
                       final: bool = False, fade_others: float = 0.0) -> Image.Image:
    """首字横转盘（RGBA 透明底全幅层）。格 k 显示 names[k%n] 的首字。

    fade_others>0：非中心格向画布底淡出（1.0=完全消失）。
    金属红指示框固定在画布中心槽；终点帧中心格红底、首字提亮。
    """
    sc = colors.get("res_scale", 1.0)
    cw, ch = int(CFG.CANVAS_W * sc), int(CFG.CANVAS_H * sc)
    cell = int(CFG.WHEEL_CELL * sc)
    fs = lambda s_: load_font(int(s_ * sc))
    cx = cw // 2
    y1 = y0 + int(28 * sc)
    layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    n = len(names)
    start_k = math.floor(x_offset / cell) - 3
    for k in range(start_k, start_k + 8):
        ch = _first_char(names[k % n])
        gx = cx + x_offset - (k + 0.5) * cell
        box = [gx - cell / 2, y0, gx + cell / 2, y1]
        is_center = abs(gx - cx) < cell / 2
        cell_fill = colors["hw_cell"]
        ch_color = colors["hw_num"]
        if fade_others > 0 and not is_center:
            keep = 1.0 - fade_others
            cell_fill = tuple(int(colors["bg"][i] * (1 - keep) + colors["hw_cell"][i] * keep)
                              for i in range(3))
            ch_color = tuple(int(colors["bg"][i] * (1 - keep) + colors["hw_num"][i] * keep)
                             for i in range(3))
        if is_center and final:
            cell_fill = CFG.WHEEL_RED_BG   # 红底先画，压在首字下面
            ch_color = CFG.WHEEL_NUM_FINAL
        d.rectangle(box, fill=cell_fill)
        d.line([box[0], y0, box[0], y1], fill=colors["hw_line"], width=max(1, int(1 * sc)))
        d.text((gx, (y0 + y1) / 2), ch, fill=ch_color, font=fs(19), anchor="mm")
    box = [cx - cell / 2, y0, cx + cell / 2, y1]
    d.rectangle(box, outline=CFG.WHEEL_RED_HI, width=max(2, int(2 * sc)))
    d.rectangle([box[0] + max(1, int(1 * sc)), box[3] - max(1, int(3 * sc)), box[2], box[3]],
                outline=CFG.WHEEL_RED_LO, width=max(2, int(2 * sc)))
    d.rectangle(box, outline=CFG.WHEEL_RED, width=max(2, int(2 * sc)))
    return layer


def _draw_reveal(img: Image.Image, names: list[str], asker_idx: int, t: float,
                 t_name: float, alpha: float, y0: int, colors: dict,
                 row_h: float, move_to: float, asker_label: str) -> None:
    """选中格双轴展开：120×104 → 与金框同尺寸（全宽 × LENS_SCALE·行高），
    红底与金属红边框保留随框扩展。

    t=格子展开进度 0..1；t_name=名字进度 0..2（前段 0..1 滚动式揭开：
    整串从「首字居中」连续左滑到「整串居中」，新字从右缘滚入；后段 1..2 放大 72→96）；
    alpha=「提问者」垫字显现（0→50%，展开完成后走第一幕垫字逻辑）。
    """
    sc = colors.get("res_scale", 1.0)
    cw, ch = int(CFG.CANVAS_W * sc), int(CFG.CANVAS_H * sc)
    cell = int(CFG.WHEEL_CELL * sc)
    fs = lambda s_: load_font(int(s_ * sc))
    cx = cw // 2
    h1 = CFG.LENS_SCALE * row_h                       # 金框高度（row_h 已按分辨率缩放）
    h0 = 27.9 * sc                                    # 横盘条原高
    y_mid0 = y0 + h0 / 2                              # 横盘条中心
    y_mid1 = (move_to + h1 / 2 + ch - int(CFG.FOOTER_H * sc)) / 2  # 金框下缘与下栏的中点
    h = h0 + (h1 - h0) * t
    y_mid = y_mid0 + (y_mid1 - y_mid0) * t
    half = cell / 2 + (cw / 2 - cell / 2) * t
    box = [cx - half, y_mid - h / 2, cx + half, y_mid + h / 2]
    d = ImageDraw.Draw(img)
    d.rectangle(box, fill=CFG.WHEEL_RED_BG)   # 选定红底保留
    d.rectangle(box, outline=CFG.WHEEL_RED_HI, width=max(2, int(2 * sc)))
    d.rectangle([box[0] + max(1, int(1 * sc)), box[3] - max(1, int(3 * sc)), box[2], box[3]],
                outline=CFG.WHEEL_RED_LO, width=max(2, int(2 * sc)))
    d.rectangle(box, outline=CFG.WHEEL_RED, width=max(2, int(2 * sc)))
    name = _sanitize_name(names[asker_idx])
    reveal = min(1.0, t_name)
    grow = max(0.0, t_name - 1.0)
    size = int(19 + 6 * grow)
    if fs(size).getlength(name) > cw - max(1, int(16 * sc)):
        size = _fit_font(name, cw - max(1, int(16 * sc)))
    name_w = fs(size).getlength(name)
    name_cx = cx + (name_w - size) / 2 * (1.0 - reveal)
    # 提问者垫字：与回答者垫字同色同字号（150px），向红底混合渐显（0→50%）；
    # 仅 alpha>0 时绘制，且裁剪到格子内
    a = min(alpha, 1.0) * CFG.LABEL_ALPHA_MAX
    if a > 0:
        wm = tuple(int(CFG.WHEEL_RED_BG[i] * (1 - a) + colors["answerer"][i] * a)
                   for i in range(3))
        wm_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        wd = ImageDraw.Draw(wm_layer)
        wd.text((cx, y_mid), asker_label, fill=wm, font=fs(CFG.ANSWERER_FONT),
                anchor="mm")
        crop = wm_layer.crop((int(box[0]), int(box[1]), int(box[2]), int(box[3])))
        img.paste(crop, (int(box[0]), int(box[1])), crop)
    # 全名最后画（压垫字之上），明亮色；裁剪到格子内（滚动入画）
    nm_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    nd = ImageDraw.Draw(nm_layer)
    nd.text((name_cx, y_mid), name, fill=CFG.WHEEL_NUM_FINAL, font=fs(size),
            anchor="mm")
    crop = nm_layer.crop((int(box[0]), int(box[1]), int(box[2]), int(box[3])))
    img.paste(crop, (int(box[0]), int(box[1])), crop)


def _act1_static_frame(colors: dict, footer: str, title: str, names: list[str],
                       n: int, target: int, s: float, row_h: float, center_y: float,
                       label: str, label_a: int, label_color: tuple,
                       label_alpha: float) -> Image.Image:
    """第一幕静止帧：滚动停在 target，金框金字；垫字按 label_alpha 渐显。"""
    sc = colors.get("res_scale", 1.0)
    off = float(CFG.CYCLE_ROUNDS * n + target)
    img = _draw_base(colors, footer, title)
    view_top = int(CFG.TITLE_H * sc)
    view_bottom = int(CFG.CANVAS_H * sc) - int(CFG.FOOTER_H * sc)
    first = math.floor(off) - 3
    for r in range(first, first + 8):
        d = r - off
        cy = center_y + _lens_y(d, s) * row_h
        if cy < view_top - 2 * row_h or cy > view_bottom + 2 * row_h:
            continue
        gold = r % n == target
        _draw_row(img, names, cy, r, _font_for(d, s), _shape_h(d, s) * row_h,
                  colors, selected=gold, gold=gold,
                  label=label if gold else None,
                  label_size=label_a if gold else None,
                  label_color=label_color if gold else None,
                  label_alpha=label_alpha if gold else 0.0,
                  label_dy=-max(1, int(1 * sc)))
    return img


# ---------- 帧序列 ----------

def _build_frames(names: list[str], target: int, asker_idx: int, colors: dict,
                  labels: tuple[str, str],
                  single_act: bool = False) -> list[tuple[Image.Image, int]]:
    """两幕帧序列 + 逐帧延迟（未按总时长缩放，render_gif 内统一缩放）。

    single_act=True：仅第一幕（滚轮选人 → 长定格），供「单人」模式使用。
    """
    n = len(names)
    s = CFG.LENS_SCALE
    sc = colors.get("res_scale", 1.0)
    cw, ch = int(CFG.CANVAS_W * sc), int(CFG.CANVAS_H * sc)
    _ldy = -max(1, int(1 * sc))
    view_top = int(CFG.TITLE_H * sc)
    view_bottom = int(CFG.CANVAS_H * sc) - int(CFG.FOOTER_H * sc)
    view_h = view_bottom - view_top
    row_h = view_h / CFG.ROW_VIEW
    center_y = (view_top + view_bottom) / 2
    footer = f"{n}/{n} 人参与"
    title, answerer_label, asker_label = labels
    label_color = colors["answerer"]
    label_a = CFG.ANSWERER_FONT
    frames: list[tuple[Image.Image, int]] = []

    # ===== 第一幕：三段运动（速度连续无卡顿） =====
    final_off = float(CFG.CYCLE_ROUNDS * n + target)
    v_fast = CFG.FAST_SPEED
    # 小名单自适应：快转不超过终点的 64%，避免冲过头
    n_fast = max(2, min(8, int(0.64 * final_off / v_fast)))
    n_brake = CFG.BRAKE_FRAMES
    # 解 v_tail：v_fast·n_fast + n_brake·(v_fast+v_tail)/2 + v_tail·n_tail/3 = final_off
    v_tail = (final_off - v_fast * n_fast - n_brake * v_fast / 2) / (
        n_brake / 2 + CFG.TAIL_FRAMES / 3)
    if v_tail < 0.05:
        v_tail = 0.05
    offsets: list[float] = []
    cur = 0.0
    for _ in range(n_fast):
        offsets.append(cur)
        cur += v_fast
    for i in range(n_brake):
        v = v_fast + (v_tail - v_fast) * (i + 0.5) / n_brake  # 中点速度
        offsets.append(cur)
        cur += v
    tail_d = max(0.2, final_off - cur)   # 缓出段总距离（∫easeOutCubic = v_tail·n_tail/3）
    for i in range(CFG.TAIL_FRAMES - 1):
        t = (i + 1) / CFG.TAIL_FRAMES
        offsets.append(cur + tail_d * (1 - (1 - t) ** 3))
    offsets.append(final_off)
    tail_start = n_fast + n_brake
    for i, off in enumerate(offsets):
        settle = i == len(offsets) - 1
        img = _draw_base(colors, footer, title)
        first = math.floor(off) - 3
        for r in range(first, first + 8):
            d = r - off
            cy = center_y + _lens_y(d, s) * row_h
            if cy < view_top - 2 * row_h or cy > view_bottom + 2 * row_h:
                continue
            gold = settle and r % n == target
            _draw_row(img, names, cy, r, _font_for(d, s), _shape_h(d, s) * row_h,
                      colors, selected=gold, gold=gold)
        if settle:
            dur = CFG.HOLD1_MS   # 「停」的节拍，垫字随后渐显
        elif i >= tail_start:
            dur = int(CFG.BASE_FRAME_MS + (CFG.TAIL_END_MS - CFG.BASE_FRAME_MS)
                      * (i - tail_start + 1) / (len(offsets) - 1 - tail_start))
        else:
            dur = CFG.BASE_FRAME_MS
        frames.append((img, dur))
    # 定格后：垫字逐渐显现（滚动静止，alpha 0→封顶值平滑五档）
    for f in range(CFG.LABEL_FADE_FRAMES):
        img = _act1_static_frame(colors, footer, title, names, n, target, s, row_h,
                                 center_y, answerer_label, label_a, label_color,
                                 (f + 1) / CFG.LABEL_FADE_FRAMES * CFG.LABEL_ALPHA_MAX)
        frames.append((img, CFG.LABEL_FADE_MS))
    if single_act:
        # 单幕：垫字全显后长驻结束
        img = _act1_static_frame(colors, footer, title, names, n, target, s, row_h,
                                 center_y, answerer_label, label_a, label_color, 1.0)
        frames.append((img, CFG.HOLD2_MS))
        return frames

    # ===== 第二幕 =====
    move_to = _move_to(s, row_h, view_top, center_y, sc)
    wheel_y0 = _wheel_y0(s, row_h, view_bottom, move_to, sc)

    # ① 其他行淡出（与第一幕定格帧严格同位；垫字自定格起保持显现）
    for f in range(CFG.FADE_FRAMES):
        alpha = 1.0 - (f + 1) / CFG.FADE_FRAMES
        img = _draw_base(colors, footer, title)
        for r in range(n):
            if r == target:
                continue
            d = r - target
            _draw_row(img, names, center_y + _lens_y(d, s) * row_h, r,
                      _font_for(d, s), _shape_h(d, s) * row_h, colors, alpha=alpha)
        _draw_row(img, names, center_y, target, _font_for(0, s), s * row_h,
                  colors, selected=True, gold=True, label=answerer_label,
                  label_size=label_a, label_color=label_color,
                  label_alpha=1.0, label_dy=_ldy)
        frames.append((img, 70))

    # ② 选中格上移 1.5 格（格高、字号、金框、垫字均不变，跟随上移）
    for f in range(CFG.MOVE_FRAMES):
        t = ease_in_out((f + 1) / CFG.MOVE_FRAMES)
        cy = center_y + (move_to - center_y) * t
        img = _draw_base(colors, footer, title)
        _draw_row(img, names, cy, target, _font_for(0, s), s * row_h, colors,
                  selected=True, gold=True, label=answerer_label, label_size=label_a,
                  label_color=label_color, label_alpha=1.0, label_dy=_ldy)
        frames.append((img, 70))

    # ③ 横转盘从右侧滑入
    final_x_off = (asker_idx + 0.5) * CFG.WHEEL_CELL
    wheel_layer = _draw_hwheel_layer(names, final_x_off, wheel_y0, colors)
    for f in range(CFG.SLIDE_FRAMES):
        t = ease_in_out((f + 1) / CFG.SLIDE_FRAMES)
        img = _draw_base(colors, footer, title)
        _draw_row(img, names, move_to, target, _font_for(0, s), s * row_h, colors,
                  selected=True, gold=True, label=answerer_label, label_size=label_a,
                  label_color=label_color, label_alpha=1.0, label_dy=_ldy)
        vis = int(cw * t)   # 可见宽度逐步展开 → 从右入画
        crop = wheel_layer.crop((cw - vis, 0, cw, ch))
        img.paste(crop, (cw - vis, 0), crop)   # RGBA 必须带 mask 粘贴
        frames.append((img, 70))

    # ④ 横转盘滚动（多滚 3 整圈；首字格停在中心红框）
    spin_total = 3 * n * CFG.WHEEL_CELL + final_x_off
    spin_offs: list[float] = []
    scur = 0.0
    for _ in range(CFG.SPIN_FAST):
        spin_offs.append(scur)
        scur += 1.5 * CFG.WHEEL_CELL
    for i in range(CFG.SPIN_DECEL):
        t = ease_in_out((i + 1) / CFG.SPIN_DECEL)
        spin_offs.append(scur + (spin_total - scur) * t)
    spin_offs.append(spin_total)
    period = n * CFG.WHEEL_CELL
    spin_durs = ([CFG.BASE_FRAME_MS] * CFG.SPIN_FAST +
                 [int(CFG.BASE_FRAME_MS + 150 * (i + 1) / CFG.SPIN_DECEL)
                  for i in range(CFG.SPIN_DECEL)])
    for i, so in enumerate(spin_offs):
        last = i == len(spin_offs) - 1
        img = _draw_base(colors, footer, title)
        _draw_row(img, names, move_to, target, _font_for(0, s), s * row_h, colors,
                  selected=True, gold=True, label=answerer_label, label_size=label_a,
                  label_color=label_color, label_alpha=1.0, label_dy=_ldy)
        layer = _draw_hwheel_layer(names, so % period, wheel_y0, colors, final=last)
        img.paste(layer, (0, 0), layer)   # RGBA 必须带 mask 粘贴
        if last:
            frames.append((img, 400))   # 「停」的节拍，随后展开
        else:
            frames.append((img, spin_durs[i]))
    # ⑤ 定格后：其他格淡出 → 选中格双轴展开（与金框同尺寸）
    # → 全名滚动式揭开并放大 72→96 → 「提问者」垫字渐显（同第一幕垫字逻辑）→ 长驻
    for f in range(CFG.FADE_FRAMES):
        img = _draw_base(colors, footer, title)
        _draw_row(img, names, move_to, target, _font_for(0, s), s * row_h, colors,
                  selected=True, gold=True, label=answerer_label, label_size=label_a,
                  label_color=label_color, label_alpha=1.0, label_dy=_ldy)
        layer = _draw_hwheel_layer(names, final_x_off, wheel_y0, colors, final=True,
                                   fade_others=(f + 1) / CFG.FADE_FRAMES)
        img.paste(layer, (0, 0), layer)
        frames.append((img, 70))
    for f in range(CFG.MOVE_FRAMES):
        t = ease_in_out((f + 1) / CFG.MOVE_FRAMES)
        img = _draw_base(colors, footer, title)
        _draw_row(img, names, move_to, target, _font_for(0, s), s * row_h, colors,
                  selected=True, gold=True, label=answerer_label, label_size=label_a,
                  label_color=label_color, label_alpha=1.0, label_dy=_ldy)
        _draw_reveal(img, names, asker_idx, t, (f + 1) / CFG.MOVE_FRAMES * 2.0, 0.0,
                     wheel_y0, colors, row_h, move_to, asker_label)
        frames.append((img, 70))
    for f in range(CFG.LABEL_FADE_FRAMES):
        img = _draw_base(colors, footer, title)
        _draw_row(img, names, move_to, target, _font_for(0, s), s * row_h, colors,
                  selected=True, gold=True, label=answerer_label, label_size=label_a,
                  label_color=label_color, label_alpha=1.0, label_dy=_ldy)
        _draw_reveal(img, names, asker_idx, 1.0, 2.0,
                     (f + 1) / CFG.LABEL_FADE_FRAMES,
                     wheel_y0, colors, row_h, move_to, asker_label)
        frames.append((img, CFG.LABEL_FADE_MS))
    img = _draw_base(colors, footer, title)
    _draw_row(img, names, move_to, target, _font_for(0, s), s * row_h, colors,
              selected=True, gold=True, label=answerer_label, label_size=label_a,
              label_color=label_color, label_alpha=1.0, label_dy=_ldy)
    _draw_reveal(img, names, asker_idx, 1.0, 2.0, 1.0,
                 wheel_y0, colors, row_h, move_to, asker_label)
    frames.append((img, CFG.HOLD2_MS))
    return frames


def render_gif(names: list[str], target: int, asker_idx: int, colors: dict,
               total_ms: int, labels: tuple[str, str],
               single_act: bool = False) -> bytes:
    """渲染动画 GIF（single_act=False 两幕；True 仅第一幕滚轮）。

    names 显示名列表；target 回答者索引；asker_idx 提问者索引（0..N-1）。
    total_ms 为配置的总时长（毫秒），按比例缩放全部帧延迟（定格帧同比例）。
    循环控制：**不写 NETSCAPE 循环扩展** —— QQ 实测把 loop 值解释为
    "额外重复次数"（loop=1 播两遍、loop=2 播三遍），不带扩展才播一遍停。
    """
    frames = _build_frames(names, target, asker_idx, colors, labels,
                           single_act=single_act)
    durations = [d for _, d in frames]
    scale = min(2.5, max(0.5, total_ms / max(1, sum(durations))))
    durations = [max(20, int(round(d * scale))) for d in durations]
    imgs = [im for im, _ in frames]
    buf = io.BytesIO()
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:],
                 duration=durations, optimize=True)
    return buf.getvalue()


def render_still(names: list[str], target: int, asker_idx: int, colors: dict,
                 labels: tuple[str, str], single_act: bool = False) -> Image.Image:
    """最终定格帧（预览/调试用，与动画最后一帧同一套绘制逻辑）。"""
    frames = _build_frames(names, target, asker_idx, colors, labels,
                           single_act=single_act)
    return frames[-1][0]
