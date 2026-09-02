"""选人器：报名 → 竖向滚轮动画选人 → 真艾特播报。

业务顺序（设计文档 11.3）：先随机确定选中者（连中降权加权随机），
再渲染动画停在选中者，发图，发文案。
"""

import asyncio
import os
import random
import re
import tempfile
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from . import config as CFG
from . import renderer, store
from .webui.api import StatsAPI

PLUGIN_NAME = "astrbot_plugin_truth_dare"


@register(
    PLUGIN_NAME,
    "Oya & Claude",
    "QQ 群选人器：报名、竖向滚轮动画选人、真艾特播报，支持连中降权与多群独立名单。",
    "0.2.2",
)
class TruthDarePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        # 统计页后端 API（Pages 机制，前端在 pages/stats/）
        self.stats_api = StatsAPI(self)
        self.stats_api.register()

    # ---------- 通用工具 ----------

    def _gid(self, event: AstrMessageEvent) -> str:
        return event.get_group_id() or "unknown"

    def _access_mode(self) -> str:
        """准入模式：开关字段 strict_mode（开=白名单/严格）；兼容旧版 access_mode 字符串。"""
        if self.config is None:
            return CFG.ACCESS_MODE_DEFAULT
        v = self.config.get("strict_mode", None)
        if v is None:
            return str(self.config.get("access_mode") or CFG.ACCESS_MODE_DEFAULT)
        return "严格" if bool(v) else "宽松"

    def _gate(self, gid: str, data: dict) -> tuple[bool, str | None]:
        """准入检查（设计文档 11 章准入模型）。

        返回 (是否放行, 需要回复的提示文本或 None)。
        - 群被关闭（enabled=False）：静默
        - 严格模式 + 未登记：带防刷冷却的提示
        豁免指令（不走 gate）：luckyenable / luckyregister / luckyunregister（管理员管理通道）。
        """
        if not data.get("enabled", True):
            return False, None
        if self._access_mode() == "严格" and not data.get("registered", False):
            if store.should_warn(gid, "strict"):
                return False, CFG.get_reply("strict_denied")
            return False, None
        return True, None

    def _names_text(self, data: dict) -> str:
        return "、".join(info["name"] for _, info in store.players_list(data))

    def _sender_display(self, event: AstrMessageEvent) -> str:
        return event.get_sender_name() or str(event.get_sender_id())

    def _coef(self) -> float:
        try:
            return float(self.config.get("weight_coef", CFG.WEIGHT_COEF)) if self.config else CFG.WEIGHT_COEF
        except (TypeError, ValueError):
            return CFG.WEIGHT_COEF

    def _pick_mode(self) -> str:
        """抽取人数：双人（两幕）/ 单人（仅滚轮）。"""
        return CFG.pick_mode_of(self.config)

    def _llm_on(self) -> bool:
        """LLM 自然语言支持开关（配置 llm_support，默认开）。"""
        if self.config is None:
            return True
        return bool(self.config.get("llm_support", True))

    @staticmethod
    def _extract_qq_from_at(text: str) -> str:
        """从 [CQ:at,qq=xxx] 或 [At:xxx] 形式的文本中提取 QQ 号。"""
        m = re.search(r"\[CQ:at,qq=(\d+)\]|\[At:(\d+)\]", text or "")
        if m:
            return m.group(1) or m.group(2)
        return (text or "").strip()

    async def _render_gif_bytes(self, names, target, asker_idx, labels, single_act):
        colors = CFG.resolve_colors(self.config)
        total_ms = CFG.total_ms_of(self.config)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, renderer.render_gif, names, target, asker_idx, colors, total_ms,
            labels, single_act)

    async def _cleanup_later(self, path: str, delay: float = 120.0):
        await asyncio.sleep(delay)
        try:
            os.remove(path)
        except OSError:
            pass

    # ---------- 报名 ----------

    async def _join_core(self, event: AstrMessageEvent, name: str) -> str:
        """报名核心逻辑（指令与 LLM 工具共用），返回回复文本（静默拦截时为空串）。"""
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            return reply or ""
        qq = str(event.get_sender_id())
        custom = (name or "").strip()
        if qq in data["players"]:
            if custom:
                data["players"][qq]["name"] = custom
                store.save_group(gid, data)
                return CFG.get_reply(
                    "join_dup", name=custom, count=len(data["players"]), names=self._names_text(data))
            return CFG.get_reply("join_already")
        max_players = CFG.max_players_of(self.config)
        if len(data["players"]) >= max_players:
            return CFG.get_reply("join_full", max=max_players)
        display = custom or self._sender_display(event)
        data["players"][qq] = {"name": display, "streak": 0, "play_count": 0}
        # 自动登记（玩过即登记）+ 顺手记录群名
        store.ensure_registered(gid)
        data = store.load_group(gid)
        data["players"][qq] = {"name": display, "streak": 0, "play_count": 0}
        try:
            grp = await event.get_group()
            if grp and getattr(grp, "group_name", None):
                data["group_name"] = grp.group_name
        except Exception:
            pass
        store.save_group(gid, data)
        store.clear_warn(gid, "quit")
        return CFG.get_reply(
            "join_ok", name=display, count=len(data["players"]), names=self._names_text(data))

    @filter.command("luckyadd", alias={"参加选人器"})
    async def luckyadd(self, event: AstrMessageEvent, name: GreedyStr):
        """报名参加选人器，可自定义显示名"""
        text = await self._join_core(event, name or "")
        if text:
            yield event.plain_result(text)

    @filter.llm_tool()
    async def td_join(self, event: AstrMessageEvent, name: str):
        """
        帮用户报名选人器。当用户说想参加/报名/加入选人器时调用。
        Args:
            name(string): 用户想用的显示名，没提到就传空字符串
        """
        if not self._llm_on():
            return "本插件未开启 LLM 自然语言支持，请使用指令（@我 选人器帮助）。"
        text = await self._join_core(event, name or "")
        return text or "本群选人器未开放，无法报名。"

    # ---------- 开转 ----------

    async def _play_core(self, event: AstrMessageEvent) -> tuple[str, bool]:
        """开转核心逻辑（指令与 LLM 工具共用）。

        返回 (文本, 已发送)：失败/拦截路径返回 (提示文本, False)；
        成功路径内部完成名单/GIF/艾特发送后返回 (结果摘要, True)。
        """
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            return (reply or ""), False
        # 每次开转都刷新群名（首次报名抓取作为兜底，这里保持跟随群改名）
        try:
            grp = await event.get_group()
            if grp and getattr(grp, "group_name", None) and grp.group_name != data.get("group_name"):
                data["group_name"] = grp.group_name
                store.save_group(gid, data)
        except Exception:
            pass
        players = store.players_list(data)
        if len(players) < CFG.MIN_PLAYERS:
            return CFG.get_reply("start_need_more", count=len(players)), False
        if store.is_spinning(gid):
            return CFG.get_reply("start_spinning"), False
        store.mark_spinning(gid, True)
        try:
            # ① 先随机确定选中者（连中降权加权随机）
            winner_qq = store.weighted_pick(data["players"], self._coef())
            winner_name = data["players"][winner_qq]["name"]
            names = [info["name"] for _, info in players]
            target = [i for i, (qq, _) in enumerate(players) if qq == winner_qq][0]
            title, answerer_label, asker_label = CFG.get_labels(self.config)
            labels = (title, answerer_label, asker_label)
            # 模式优先取本群覆盖（选人器人数/动画 指令写入），否则用插件配置
            mode = data.get("pick_mode") or self._pick_mode()
            single_act = mode == "单人"
            text_only = data.get("text_mode", CFG.text_mode_of(self.config))
            # ② 双人/文本模式：抽提问者（排除回答者，其余等概率；横盘停在首字格=索引）
            asker_qq = asker_idx = None
            if not single_act:
                others = [i for i, (qq, _) in enumerate(players) if qq != winner_qq]
                asker_idx = others[random.randrange(len(others))]
                asker_qq, asker_info = players[asker_idx]
            # ③ 渲染动画（文本模式跳过，纯文本极速播报；失败则不改任何状态）
            gif_bytes = None
            if not text_only:
                try:
                    gif_bytes = await self._render_gif_bytes(
                        names, target, asker_idx if asker_idx is not None else 0,
                        labels, single_act)
                except Exception as e:
                    logger.error(f"[truth_dare] GIF 渲染失败: {e}")
                    return "转盘生成失败，请稍后再试", False
            # ④ 提交本局状态与历史快照（设计文档 10.8：播报准备完成后才提交）
            store.apply_result(data, winner_qq, self._coef())
            snapshot = {
                "ts": int(time.time()),
                "players": [{"qq": qq, "name": info["name"]} for qq, info in players],
                "winner": {"qq": winner_qq, "name": winner_name},
            }
            if not single_act:
                snapshot["asker"] = {"qq": asker_qq, "name": asker_info["name"]}
            store.add_history(data, snapshot)
            store.save_group(gid, data)
            # ⑤ 播报 + 真艾特
            path = None
            if gif_bytes is not None:
                fd, path = tempfile.mkstemp(suffix=".gif", prefix="truthdare_")
                with os.fdopen(fd, "wb") as f:
                    f.write(gif_bytes)
                asyncio.create_task(self._cleanup_later(path))
            roster = " ".join(f"[{i + 1}]{info['name']}" for i, (qq, info) in enumerate(players))
            # ⑤b 播报：文本消息与艾特消息分开发送——
            # 注意不能用 yield 两条 chain_result：AstrBot 会把同一处理器 yield 的所有链
            # 合并成一条消息；QQ 又会吃掉文本段的段尾换行。改用 event.send 直接发两条。
            mention = CFG.mention_enabled_of(self.config)
            if text_only and single_act:
                await event.send(MessageChain([
                    Plain(CFG.get_reply("play_roster", roster=roster) + "\n"
                          + CFG.get_reply("winner_text", answerer=answerer_label,
                                          winner=winner_name)),
                ]))
                if mention:
                    await event.send(MessageChain([At(qq=winner_qq)]))
                summary = CFG.get_reply("winner_text", answerer=answerer_label, winner=winner_name)
            elif text_only:
                await event.send(MessageChain([
                    Plain(CFG.get_reply("play_roster", roster=roster) + "\n"
                          + CFG.get_reply("play_result", answerer=answerer_label,
                                          winner=winner_name, asker=asker_label,
                                          asker_name=asker_info["name"])),
                ]))
                if mention:
                    await event.send(MessageChain([At(qq=winner_qq), At(qq=asker_qq)]))
                summary = CFG.get_reply("play_result", answerer=answerer_label,
                                        winner=winner_name, asker=asker_label,
                                        asker_name=asker_info["name"])
            elif single_act:
                await event.send(MessageChain([
                    Plain(CFG.get_reply("play_roster", roster=roster)),
                    Image(file=path),
                    Plain(CFG.get_reply("winner_text", answerer=answerer_label,
                                        winner=winner_name)),
                ]))
                if mention:
                    await event.send(MessageChain([At(qq=winner_qq)]))
                summary = CFG.get_reply("winner_text", answerer=answerer_label, winner=winner_name)
            else:
                await event.send(MessageChain([
                    Plain(CFG.get_reply("play_roster", roster=roster)),
                    Image(file=path),
                    Plain(CFG.get_reply("play_result", answerer=answerer_label, winner=winner_name,
                                        asker=asker_label, asker_name=asker_info["name"])),
                ]))
                if mention:
                    await event.send(MessageChain([At(qq=winner_qq), At(qq=asker_qq)]))
                summary = CFG.get_reply("play_result", answerer=answerer_label,
                                        winner=winner_name, asker=asker_label,
                                        asker_name=asker_info["name"])
            return summary, True
        finally:
            store.mark_spinning(gid, False)

    @filter.command("luckyplay", alias={"选人器开始"})
    async def luckyplay(self, event: AstrMessageEvent):
        """开始抽选（按当前模式播报滚轮动画或纯文本）"""
        text, sent = await self._play_core(event)
        if text and not sent:
            yield event.plain_result(text)

    @filter.llm_tool()
    async def td_start(self, event: AstrMessageEvent):
        """开始一轮选人器抽选。当用户说开始/开转/抽一个/玩一局时调用。名单与动画会直接发到群里。"""
        if not self._llm_on():
            return "本插件未开启 LLM 自然语言支持，请使用指令（@我 选人器帮助）。"
        text, _ = await self._play_core(event)
        return text or "本群选人器未开放，无法开始。"

    # ---------- 帮助 ----------

    @filter.command("luckyhelp", alias={"选人器帮助"})
    async def luckyhelp(self, event: AstrMessageEvent):
        """玩法与常用指令说明（文案可在插件配置自定义）"""
        text = ""
        if self.config is not None:
            text = str(self.config.get("help_text_member", "") or "").strip()
        yield event.plain_result(text or CFG.get_reply("help_member"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("luckycmds", alias={"选人器指令"})
    async def luckycmds(self, event: AstrMessageEvent):
        """管理员：全部管理指令清单（文案可在插件配置自定义）"""
        text = ""
        if self.config is not None:
            text = str(self.config.get("help_text_admin", "") or "").strip()
        yield event.plain_result(text or CFG.get_reply("help_admin"))

    # ---------- 模式/动画切换（按群存储覆盖，所有人可用） ----------

    @filter.command("luckymode", alias={"选人器人数"})
    async def luckymode(self, event: AstrMessageEvent, arg: GreedyStr):
        """查看或切换抽取人数（1=一人，2=两人）"""
        gid = self._gid(event)
        data = store.load_group(gid)
        arg = (arg or "").strip()
        if arg in ("1", "一", "单人", "一人"):
            data["pick_mode"] = "单人"
            store.save_group(gid, data)
            yield event.plain_result(CFG.get_reply("mode_set", label="一人"))
        elif arg in ("2", "二", "双人", "两人"):
            data["pick_mode"] = "双人"
            store.save_group(gid, data)
            yield event.plain_result(CFG.get_reply("mode_set", label="两人"))
        elif arg:
            yield event.plain_result(CFG.get_reply("mode_bad"))
        else:
            mode = data.get("pick_mode") or self._pick_mode()
            label = "两人" if mode == "双人" else "一人"
            anim = "关" if data.get("text_mode", CFG.text_mode_of(self.config)) else "开"
            yield event.plain_result(CFG.get_reply("mode_show", label=label, anim=anim))

    @filter.command("luckyanim", alias={"选人器动画"})
    async def luckyanim(self, event: AstrMessageEvent, arg: GreedyStr):
        """查看或切换动画开关（开=GIF，关=纯文本极速）"""
        gid = self._gid(event)
        data = store.load_group(gid)
        arg = (arg or "").strip()
        if arg == "开":
            data["text_mode"] = False
            store.save_group(gid, data)
            yield event.plain_result(CFG.get_reply("anim_on"))
        elif arg == "关":
            data["text_mode"] = True
            store.save_group(gid, data)
            yield event.plain_result(CFG.get_reply("anim_off"))
        elif arg:
            yield event.plain_result(CFG.get_reply("anim_bad"))
        else:
            on = not data.get("text_mode", CFG.text_mode_of(self.config))
            yield event.plain_result(CFG.get_reply("anim_show", state="开" if on else "关"))

    # ---------- 战绩查询 ----------

    def _rank_core(self, event: AstrMessageEvent) -> str:
        """本群战绩统计核心（前 5 排行），返回回复文本。"""
        gid = self._gid(event)
        data = store.load_group(gid)
        hist = data.get("history", [])
        if not hist:
            return CFG.get_reply("rank_empty")
        counts: dict[str, dict] = {}
        for h in hist:
            wq = str(h.get("winner", {}).get("qq", ""))
            if not wq:
                continue
            if wq not in counts:
                counts[wq] = {"name": h["winner"].get("name", ""), "n": 0}
            counts[wq]["n"] += 1
        top = sorted(counts.items(), key=lambda kv: -kv[1]["n"])[:5]
        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        lines = "\n".join(f"{medals[i]} {v['name']} {v['n']} 次" for i, (_, v) in enumerate(top))
        name = data.get("alias") or data.get("group_name") or gid
        return CFG.get_reply(
            "rank_group", name=name, plays=len(hist),
            count=len(data.get("players", {})), lines=lines)

    @filter.command("luckyrank", alias={"选人器排行榜"})
    async def luckyrank(self, event: AstrMessageEvent):
        """本群战绩统计（前 5 排行）"""
        yield event.plain_result(self._rank_core(event))

    @filter.llm_tool()
    async def td_rank(self, event: AstrMessageEvent):
        """查看本群选人器战绩排行。当用户问谁赢得多/战绩/排行时调用。"""
        if not self._llm_on():
            return "本插件未开启 LLM 自然语言支持，请使用指令（@我 选人器帮助）。"
        return self._rank_core(event)

    @filter.command("luckygrank", alias={"选人器群排行榜"})
    async def luckygrank(self, event: AstrMessageEvent):
        """全群战绩统计（跨群前 5 排行）"""
        counts: dict[str, dict] = {}
        total = 0
        groups = 0
        players_qq: set[str] = set()
        for gid in store.list_all_groups():
            d = store.load_group(gid)
            h = d.get("history", [])
            if h:
                groups += 1
            total += len(h)
            players_qq.update(d.get("players", {}).keys())
            for x in h:
                wq = str(x.get("winner", {}).get("qq", ""))
                if not wq:
                    continue
                if wq not in counts:
                    counts[wq] = {"name": x["winner"].get("name", ""), "n": 0}
                counts[wq]["n"] += 1
        if total == 0:
            yield event.plain_result(CFG.get_reply("grank_empty"))
            return
        top = sorted(counts.items(), key=lambda kv: -kv[1]["n"])[:5]
        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        lines = "\n".join(f"{medals[i]} {v['name']} {v['n']} 次" for i, (_, v) in enumerate(top))
        yield event.plain_result(CFG.get_reply(
            "rank_all", groups=groups, players=len(players_qq), plays=total, lines=lines))

    @filter.command("luckystatus", alias={"选人器状态"})
    async def luckystatus(self, event: AstrMessageEvent):
        """本群状态自查（开启/登记/模式/动画/人数）"""
        yield event.plain_result(self._status_core(event))

    @filter.llm_tool()
    async def td_status(self, event: AstrMessageEvent):
        """查看本群选人器状态。当用户问玩法开着没/怎么玩/当前设置时调用。"""
        if not self._llm_on():
            return "本插件未开启 LLM 自然语言支持，请使用指令（@我 选人器帮助）。"
        return self._status_core(event)

    def _status_core(self, event: AstrMessageEvent) -> str:
        """本群状态核心，返回状态文本。"""
        gid = self._gid(event)
        data = store.load_group(gid)
        mode = data.get("pick_mode") or self._pick_mode()
        label = "两人" if mode == "双人" else "一人"
        anim = "关" if data.get("text_mode", CFG.text_mode_of(self.config)) else "开"
        return CFG.get_reply(
            "status",
            on="是" if data.get("enabled", True) else "否",
            reg="是" if data.get("registered", False) else "否",
            label=label, anim=anim, count=len(data.get("players", {})))

    # ---------- 名单查询 ----------

    def _list_core(self, event: AstrMessageEvent) -> str:
        """名单查询核心，返回回复文本（静默拦截时为空串）。"""
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            return reply or ""
        players = store.players_list(data)
        if not players:
            return CFG.get_reply("list_empty", title=CFG.get_labels(self.config)[0])
        return CFG.get_reply("list_players", count=len(players), names=self._names_text(data))

    @filter.command("luckylist", alias={"选人器玩家"})
    async def luckylist(self, event: AstrMessageEvent):
        """查看当前名单"""
        text = self._list_core(event)
        if text:
            yield event.plain_result(text)

    @filter.llm_tool()
    async def td_roster(self, event: AstrMessageEvent):
        """查看本群选人器报名名单。当用户问现在有谁/名单/参加的人/几个人时调用。"""
        if not self._llm_on():
            return "本插件未开启 LLM 自然语言支持，请使用指令（@我 选人器帮助）。"
        text = self._list_core(event)
        return text or "本群选人器未开放，无法查看名单。"

    # ---------- 退出 ----------

    async def _quit_core(self, event: AstrMessageEvent) -> str:
        """退出游戏核心，返回回复文本（静默拦截时为空串）。"""
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            return reply or ""
        qq = str(event.get_sender_id())
        if qq not in data["players"]:
            if store.should_warn(gid, "quit"):
                return CFG.get_reply("quit_not_in")
            return ""
        name = data["players"][qq]["name"]
        del data["players"][qq]
        store.save_group(gid, data)
        return CFG.get_reply("quit_ok", name=name, count=len(data["players"]), names=self._names_text(data))

    @filter.command("luckyquit", alias={"选人器退出"})
    async def luckyquit(self, event: AstrMessageEvent):
        """退出游戏"""
        text = await self._quit_core(event)
        if text:
            yield event.plain_result(text)

    @filter.llm_tool()
    async def td_quit(self, event: AstrMessageEvent):
        """帮用户退出选人器。当用户说退出/不玩了/把我移出名单时调用。"""
        if not self._llm_on():
            return "本插件未开启 LLM 自然语言支持，请使用指令（@我 选人器帮助）。"
        text = await self._quit_core(event)
        return text or "本群选人器未开放，无法退出。"

    # ---------- 重置 ----------

    @filter.command("luckyreset", alias={"选人器重置"})
    async def luckyreset(self, event: AstrMessageEvent):
        """清空本群名单"""
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            if reply:
                yield event.plain_result(reply)
            return
        data["players"] = {}
        store.save_group(gid, data)
        yield event.plain_result(CFG.get_reply("reset_ok"))

    # ---------- 移除玩家（全员可用） ----------

    @filter.command("luckyremove", alias={"选人器玩家移除"})
    async def luckyremove(self, event: AstrMessageEvent, target: GreedyStr):
        """按名字或 QQ 号把某人移出名单（全员可用）"""
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            if reply:
                yield event.plain_result(reply)
            return
        clean = self._extract_qq_from_at(target or "")
        matches = store.find_player(data, clean)
        if not matches:
            yield event.plain_result(CFG.get_reply("remove_not_found", target=clean))
            return
        if len(matches) > 1:
            desc = "、".join(f"{data['players'][qq]['name']}({qq})" for qq in matches)
            yield event.plain_result(CFG.get_reply("remove_ambiguous", matches=desc))
            return
        qq = matches[0]
        name = data["players"][qq]["name"]
        del data["players"][qq]
        store.save_group(gid, data)
        yield event.plain_result(CFG.get_reply("remove_ok", name=name, count=len(data["players"]), names=self._names_text(data)))

    # ---------- 管理员：玩法开关 ----------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("luckydisable", alias={"选人器关闭"})
    async def luckydisable(self, event: AstrMessageEvent):
        """管理员：关闭本群玩法"""
        gid = self._gid(event)
        data = store.load_group(gid)
        ok, reply = self._gate(gid, data)
        if not ok:
            if reply:
                yield event.plain_result(reply)
            return
        data["enabled"] = False
        store.save_group(gid, data)
        yield event.plain_result(CFG.get_reply(
            "disable_ok", title=CFG.get_labels(self.config)[0]))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("luckyenable", alias={"选人器开启"})
    async def luckyenable(self, event: AstrMessageEvent):
        """管理员：开启本群玩法"""
        gid = self._gid(event)
        data = store.load_group(gid)
        data["enabled"] = True
        store.save_group(gid, data)
        yield event.plain_result(CFG.get_reply(
            "enable_ok", title=CFG.get_labels(self.config)[0]))

    # ---------- 管理员：群登记（统计预留，UI 之后做） ----------

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("luckyregister", alias={"选人器群添加"})
    async def luckyregister(self, event: AstrMessageEvent):
        """管理员：登记本群（严格模式准入）"""
        gid = self._gid(event)
        store.register_group(gid)
        yield event.plain_result(CFG.get_reply("register_ok"))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("luckyunregister", alias={"选人器群删除"})
    async def luckyunregister(self, event: AstrMessageEvent):
        """管理员：移出登记并清空本群数据"""
        gid = self._gid(event)
        store.unregister_group(gid)
        yield event.plain_result(CFG.get_reply("unregister_ok"))
