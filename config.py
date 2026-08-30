"""集中配置：尺寸、颜色、动画、规则常量与全部回复文案。

规则（设计文档 10.2）：所有可调参数集中于此，禁止在业务代码中散落魔数。
回复文案集中在 REPLIES（中文默认），预留 i18n 翻译接口：
后续支持多语言时，按语言 code 扩展 REPLIES 即可。
"""

# ---------- 画布 ----------
CANVAS_W = 150
CANVAS_H = 150
TITLE_H = 17
FOOTER_H = 15
ROW_VIEW = 4.5

# ---------- 动画（两幕：第一幕选回答者 → 第二幕选提问者） ----------
# 注意：Pillow 会合并完全相同的连续帧；定格帧仅 1 帧，靠长延迟驻留
# 循环控制：不写 NETSCAPE 扩展（QQ 实测：loop 值 = 额外重复次数，
# loop=1 播两遍、loop=2 播三遍；不带扩展播一遍停，见 renderer.render_gif）

# 第一幕三段运动（速度连续无卡顿）：快速 → 刹车（线性减速）→ 长缓出（easeOutCubic）
FAST_SPEED = 1.6          # 快速段每帧滚动行数
BRAKE_FRAMES = 4          # 刹车段帧数
TAIL_FRAMES = 16          # 缓出段帧数（长尾入位，最后一帧贴到定格）
TAIL_END_MS = 180         # 缓出段最后一帧时长（越慢越稳）
HOLD1_MS = 500            # 第一幕「停」的节拍
LABEL_FADE_FRAMES = 5     # 定格后垫字渐显帧数
LABEL_FADE_MS = 80        # 垫字渐显单帧时长
# 第二幕：其他行淡出 → 金框行上移 1.5 格 → 数字横转盘滑入 → 滚动减速
FADE_FRAMES = 6
MOVE_FRAMES = 8
SLIDE_FRAMES = 6
SPIN_FAST = 12            # 横转盘快速滚动帧数
SPIN_DECEL = 10           # 横转盘减速帧数
HOLD2_MS = 1800           # 最终定格长驻
BASE_FRAME_MS = 45        # 基准帧时长；配置总时长按比例缩放全部帧延迟
# 菱形剖面：中心格放大 LENS_SCALE 倍、邻格/次邻格收缩（严格单调，总量≈4.5 格视口）
LENS_SCALE = 2.0
# 横转盘
WHEEL_CELL = 32          # 数字格宽（一位/两位数均可容纳）
# 角色标签（垫在金字下方，≤3 个汉字，可配置；回答者/提问者垫字同字号同色系）
ANSWERER_FONT = 40
LABEL_ALPHA_MAX = 0.5    # 垫字最大不透明度：窄昵称时垫字大片露出会显「底色变深」，封顶 50% 只留影子感
TITLE_DEFAULT = "选人器"   # 动画顶部标题（可配置，≤10 字符，留空/超长回退默认）
ANSWERER_LABEL_DEFAULT = "回答者"
ASKER_LABEL_DEFAULT = "提问者"

# ---------- 字体（运行时解析链，不打包字体，插件本体 <1MB） ----------
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",           # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",             # 微软雅黑
    r"C:\Windows\Fonts\NotoSansCJKsc-Bold.otf",
    r"C:\Windows\Fonts\NotoSansSC-Bold.otf",
]

# ---------- 主题（整体配色，v0.2 起含回答者垫字/横转盘三件套） ----------
# 浅色主题已按 QQ 实测验收版配色（2026-08-29）
THEMES = {
    "dark": {
        "bg": (20, 20, 24),
        "zebra_a": (40, 40, 48),
        "zebra_b": (28, 28, 34),
        "text": (245, 245, 245),
        "title_bg": (14, 14, 18),
        "title_text": (180, 180, 188),
        "line": (10, 10, 12),
        "answerer": (0x20, 0x1D, 0x1D),          # 回答者垫字 #201d1d
        "hw_cell": (32, 32, 40),                # 横转盘格子底
        "hw_num": (220, 220, 228),              # 横转盘数字
        "hw_line": (10, 10, 12),                # 横转盘格线
    },
    "light": {
        "bg": (246, 246, 250),
        "zebra_a": (255, 255, 255),
        "zebra_b": (236, 236, 242),
        "text": (32, 32, 40),
        "title_bg": (238, 238, 244),
        "title_text": (90, 90, 100),
        "line": (214, 214, 224),
        "answerer": (0xD4, 0xD4, 0xDC),          # 浅色垫字：浅灰水印，不压金字
        "hw_cell": (255, 255, 255),
        "hw_num": (32, 32, 40),
        "hw_line": (196, 196, 210),
    },
}

# ---------- 选中样式 ----------
SELECTED_GOLD = (232, 190, 80)
SELECTED_BOX_WIDTH = 4
SELECTED_TEXT_STROKE = 0    # 选中字描边宽度（0=关闭）

# ---------- 横转盘金属红 / 提问者（两主题共用） ----------
WHEEL_RED = (192, 57, 43)        # 金属红主色
WHEEL_RED_HI = (226, 88, 70)     # 高光
WHEEL_RED_LO = (140, 38, 28)     # 暗部
WHEEL_RED_BG = (64, 20, 14)      # 终点格红底（压在首字下方）
WHEEL_NUM_FINAL = (255, 240, 240)  # 终点首字/全名提亮

# ---------- 规则 ----------
MIN_PLAYERS = 2
MAX_PLAYERS = 20
WEIGHT_COEF = 1.5           # 连中降权系数（可配置）：权重 = 1/(1+连中×系数)
NAME_MAX_CHARS = 10         # 渲染截断上限
CYCLE_ROUNDS = 3            # 滚动循环圈数
ACCESS_MODE_DEFAULT = "宽松"  # 准入模式：宽松（黑名单，默认全开）/ 严格（白名单，仅登记群）
PICK_MODE_DEFAULT = "双人"   # 抽取人数：双人（回答者+提问者，两幕）/ 单人（仅回答者，单幕）

# ---------- 回复文案（i18n 预留：REPLIES[语言]） ----------
REPLIES = {
    "zh-CN": {
        "join_ok": "✅ {name} 已加入！当前 {count} 人：{names}",
        "join_dup": "✅ 显示名已更新为 {name}！当前 {count} 人：{names}",
        "join_already": "你已在名单中啦～",
        "join_full": "报名人数已满（最多 {max} 人）",
        "start_need_more": "至少需要 2 人才能开始哦，当前 {count} 人",
        "start_spinning": "正在抽选中，稍等一下～",
        "list_players": "当前 {count} 人：{names}",
        "list_empty": "还没有人报名哦，快 @我 参加{title} 吧！",
        "quit_ok": "{name} 已退出！当前 {count} 人：{names}",
        "quit_not_in": "你还未加入游戏",
        "reset_ok": "名单已清空",
        "remove_not_found": "找不到玩家：{target}",
        "remove_ambiguous": "找到多个匹配：{matches}，请用 QQ 号指定",
        "remove_ok": "已移除 {name}！当前 {count} 人：{names}",
        "play_roster": "当前参加游戏的有：\n{roster}",
        "play_result": "🎉 被选中！\n{answerer}：{winner}\n{asker}：{asker_name}",
        "winner_text": "🎉 恭喜被选中！\n{answerer}：{winner}",
        # 帮助与状态查询
        "help_member": "📖 选人器玩法\n\n报名：@我 参加选人器 名字\n开转：@我 选人器开始\n\n┈ 常用指令 ┈\n选人器玩家 —— 查看名单\n选人器退出 —— 退出游戏\n选人器重置 —— 清空名单\n选人器人数 1|2 —— 切换人数（1=一人，2=两人；不加参数查看当前）\n选人器动画 开|关 —— 动画开关（关=纯文本极速）\n选人器排行榜 —— 本群战绩统计\n选人器群排行榜 —— 全群战绩统计\n选人器状态 —— 本群状态\n\n管理指令见「选人器指令」（仅管理员可用）",
        "help_admin": "🛠 选人器管理指令\n\n选人器玩家移除 名字/QQ —— 移除玩家\n选人器开启 —— 本群玩法开启\n选人器关闭 —— 本群玩法关闭\n选人器群添加 —— 登记本群\n选人器群删除 —— 移出登记并清空数据\n选人器人数 1|2 —— 切换抽取人数（所有人可用）\n选人器动画 开|关 —— GIF/文本切换（所有人可用）\n选人器重置 —— 清空本群名单（所有人可用）",
        "mode_show": "当前抽取人数：{label} · 动画：{anim}",
        "mode_set": "已切换为{label}模式（本群生效）",
        "mode_bad": "参数请填 1（一人）或 2（两人），如：选人器人数 2",
        "anim_on": "动画已开启（滚轮 GIF 模式）",
        "anim_off": "动画已关闭（纯文本极速模式）",
        "anim_bad": "参数请填 开 或 关，如：选人器动画 关",
        "anim_show": "当前动画：{state}",
        "rank_group": "📊 {name} 统计（累计）\n总游玩：{plays} 局 · 玩家：{count} 人\n排行榜：\n{lines}",
        "rank_empty": "本群还没有游玩记录",
        "rank_all": "📊 全群统计（累计）\n参与群聊：{groups} 个 · 玩家：{players} 人 · 总游玩：{plays} 局\n排行榜：\n{lines}",
        "grank_empty": "还没有任何群的游玩记录",
        "status": "📋 选人器状态\n开启：{on} · 登记：{reg}\n抽取人数：{label} · 动画：{anim}\n当前 {count} 人",
        "enable_ok": "{title}已开启！",
        "disable_ok": "{title}已关闭",
        "register_ok": "本群已登记，开始记录游玩数据",
        "unregister_ok": "本群已移除，数据已清空",
        "strict_denied": "本群未开放，请联系管理员登记",
    },
}


def get_reply(key: str, lang: str = "zh-CN", **kw) -> str:
    t = REPLIES.get(lang, REPLIES["zh-CN"]).get(key, "")
    return t.format(**kw) if kw else t


def resolve_colors(plugin_cfg) -> dict:
    """从插件配置合成渲染配色。plugin_cfg 为 self.config（可能为 None）。

    返回约定（renderer 消费）：
    - 主题键 + "selected"（gold/box_width/stroke，常量，自定义配色功能已移除）
    - "bar_opaque"：bool，False = 透明栏（只画文字）
    - "background"：可选 PIL Image，整张画布底图（背景图功能预留，当前恒为 None）
    """
    theme_name = "dark"
    if plugin_cfg is not None:
        theme_name = str(plugin_cfg.get("theme", "dark") or "dark")
    colors = dict(THEMES.get(theme_name, THEMES["dark"]))
    colors["selected"] = {
        "gold": SELECTED_GOLD,
        "box_width": SELECTED_BOX_WIDTH,
        "stroke": SELECTED_TEXT_STROKE,
    }
    bar_opaque = True
    if plugin_cfg is not None:
        # 透明栏：配置值"透明"则只画栏文字
        bar_opaque = str(plugin_cfg.get("bar_mode", "不透明")) != "透明"
    colors["bar_opaque"] = bar_opaque
    colors["background"] = None  # 背景图功能预留入口
    colors["res_scale"] = res_of(plugin_cfg) / 150  # 分辨率因子（renderer 全量等比缩放）
    return colors


def get_labels(plugin_cfg) -> tuple[str, str, str]:
    """(标题, 回答者标签, 提问者标签) 文字。

    回答者/提问者：非空、≤3 个汉字；标题：非空、≤10 字符。
    空值/超长/非法逐项回退默认（防呆）。
    """
    def clean3(key: str, default: str) -> str:
        v = ""
        if plugin_cfg is not None:
            v = str(plugin_cfg.get(key, "") or "").strip()
        if not v or len(v) > 3 or not all("一" <= c <= "鿿" for c in v):
            return default
        return v

    def clean_title() -> str:
        v = ""
        if plugin_cfg is not None:
            v = str(plugin_cfg.get("title_text", "") or "").strip()
        if not v or len(v) > 10:
            return TITLE_DEFAULT
        return v

    return (clean_title(),
            clean3("answerer_label", ANSWERER_LABEL_DEFAULT),
            clean3("asker_label", ASKER_LABEL_DEFAULT))


def total_ms_of(plugin_cfg) -> int:
    """配置的 GIF 总时长（毫秒），非法值回退 6500。"""
    try:
        return max(1000, int(plugin_cfg.get("gif_duration_ms", 6500)))
    except (TypeError, ValueError):
        return 6500


def pick_mode_of(plugin_cfg) -> str:
    """抽取人数：双人 / 单人（非法值回退双人）。"""
    v = ""
    if plugin_cfg is not None:
        v = str(plugin_cfg.get("pick_mode", "") or "").strip()
    return v if v in ("双人", "单人") else PICK_MODE_DEFAULT


def text_mode_of(plugin_cfg) -> bool:
    """文本模式：开 = 不生成 GIF，纯文本极速播报。

    兼容旧配置：pick_mode="文本" 视为开启（配置拆分前的取值）。
    """
    if plugin_cfg is None:
        return False
    if str(plugin_cfg.get("pick_mode", "")) == "文本":
        return True
    return bool(plugin_cfg.get("text_mode", False))


def mention_enabled_of(plugin_cfg) -> bool:
    """艾特提醒：开 = 抽选后另发一条艾特消息（默认开）。"""
    if plugin_cfg is None:
        return True
    return bool(plugin_cfg.get("mention_enabled", True))


def max_players_of(plugin_cfg) -> int:
    """报名人数上限（无上限，但必须 ≥2；非法值回退默认 20）。"""
    if plugin_cfg is None:
        return MAX_PLAYERS
    try:
        return max(2, int(plugin_cfg.get("max_players", MAX_PLAYERS)))
    except (TypeError, ValueError):
        return MAX_PLAYERS


RES_OPTIONS = (150, 300)   # 动画分辨率档位：标准 / 高清（点开不糊）
RES_DEFAULT = 150


def res_of(plugin_cfg) -> int:
    """动画分辨率档位（150 标准 / 300 高清，非法值回退 150）。"""
    if plugin_cfg is None:
        return RES_DEFAULT
    try:
        r = int(plugin_cfg.get("resolution", RES_DEFAULT))
    except (TypeError, ValueError):
        return RES_DEFAULT
    return r if r in RES_OPTIONS else RES_DEFAULT
