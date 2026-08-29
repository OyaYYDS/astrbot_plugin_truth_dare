"""统计页后端 API（AstrBot Pages 机制）。

路由经 context.register_web_api 注册后，自动挂到
/api/v1/plugins/extensions/astrbot_plugin_truth_dare/<route>，
前端通过 window.AstrBotPluginPage bridge 的相对路径调用。
"""

from datetime import datetime, timedelta

from astrbot.api.web import json_response, request

from .. import store


def _range_start(range_key: str) -> int | None:
    """时间范围起点时间戳（秒）。total 返回 None（全部）。

    日=今日0点；周=本周一0点；月=本月1日0点；年=今年1月1日0点。
    """
    now = datetime.now()
    if range_key == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_key == "week":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    elif range_key == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif range_key == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return int(start.timestamp())


def _group_summary(data: dict, gid: str) -> dict:
    history = data.get("history", [])
    last_ts = history[-1].get("ts") if history else None
    return {
        "gid": gid,
        "group_name": data.get("group_name", "") or "",
        "alias": data.get("alias", "") or "",
        "registered": bool(data.get("registered", False)),
        "enabled": bool(data.get("enabled", True)),
        "total_plays": int(data.get("total_plays", 0)),
        "player_count": len(data.get("players", {})),
        "last_active_ts": last_ts,
    }


class StatsAPI:
    def __init__(self, plugin):
        self.plugin = plugin

    def register(self):
        ctx = self.plugin.context
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups",
            self.list_groups,
            ["GET"],
            "统计页：群列表",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups/register",
            self.register_group,
            ["POST"],
            "统计页：登记群",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups/<gid>/detail",
            self.group_detail,
            ["GET"],
            "统计页：群详情（支持 range=total/day/week/month/year）",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups/<gid>/sparkline",
            self.sparkline,
            ["GET"],
            "统计页：近 N 天每日局数（days=7|30）",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups/<gid>/alias",
            self.set_alias,
            ["POST"],
            "统计页：设置群别名",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups/<gid>/reset",
            self.reset_group,
            ["POST"],
            "统计页：清空名单（保留登记与历史）",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/groups/<gid>/delete",
            self.delete_group,
            ["POST"],
            "统计页：删除群（移出登记并清空全部数据，不可逆）",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/overview",
            self.overview,
            ["GET"],
            "统计页：全群总览（支持 range=total/day/week/month/year）",
        )
        ctx.register_web_api(
            "/astrbot_plugin_truth_dare/overview/sparkline",
            self.overview_sparkline,
            ["GET"],
            "统计页：全群每日局数（days=7|30）",
        )

    async def list_groups(self):
        groups = []
        for gid in store.list_all_groups():
            groups.append(_group_summary(store.load_group(gid), gid))
        return json_response({"groups": groups})

    async def register_group(self):
        payload = await request.json(default={})
        gid = str(payload.get("gid", "")).strip()
        if not gid:
            return json_response({"ok": False, "error": "群号不能为空"})
        store.register_group(gid)
        return json_response({"ok": True})

    async def group_detail(self, gid: str):
        range_key = request.query.get("range", "total")
        data = store.load_group(gid)
        start = _range_start(range_key)
        history = data.get("history", [])
        h_in = [h for h in history if start is None or int(h.get("ts", 0)) >= start]
        # 按范围重算每人被选中次数（连中 streak 仍是当前值，不受范围影响）
        counts: dict[str, int] = {}
        for h in h_in:
            wq = str(h.get("winner", {}).get("qq", ""))
            counts[wq] = counts.get(wq, 0) + 1
        players = [
            {
                "qq": qq,
                "name": info.get("name", ""),
                "play_count": counts.get(qq, 0),
                "streak": int(info.get("streak", 0)),
            }
            for qq, info in store.players_list(data)
        ]
        return json_response({
            **_group_summary(data, gid),
            "range": range_key,
            "plays_in_range": len(h_in),
            "players": players,
            "history": list(reversed(h_in))[:20],
        })

    async def sparkline(self, gid: str):
        days = request.query.get("days", 7, type=int)
        days = max(1, min(days, 30))
        data = store.load_group(gid)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        counts: dict[datetime, int] = {}
        for h in data.get("history", []):
            d = datetime.fromtimestamp(int(h.get("ts", 0))).replace(
                hour=0, minute=0, second=0, microsecond=0)
            counts[d] = counts.get(d, 0) + 1
        points = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            points.append({"date": d.strftime("%m-%d"), "count": counts.get(d, 0)})
        return json_response({"points": points})

    async def overview(self):
        """全群总览：按 QQ 合并的玩家排行（前 10）、跨群最近 20 局、三卡片指标。"""
        range_key = request.query.get("range", "total")
        start = _range_start(range_key)
        gids = store.list_all_groups()
        total_plays = 0
        groups_with_play = 0
        last_ts = None
        player_stats: dict[str, dict] = {}   # qq -> {name, count, groups:set}
        history_all: list[dict] = []
        players_qq: set[str] = set()
        for gid in gids:
            data = store.load_group(gid)
            players_qq.update(data.get("players", {}).keys())
            h_in = [h for h in data.get("history", [])
                    if start is None or int(h.get("ts", 0)) >= start]
            if h_in:
                groups_with_play += 1
            total_plays += len(h_in)
            display = data.get("alias") or data.get("group_name") or gid
            for h in h_in:
                wq = str(h.get("winner", {}).get("qq", ""))
                if wq:
                    ps = player_stats.setdefault(
                        wq, {"name": h["winner"].get("name", ""), "count": 0, "groups": set()})
                    ps["count"] += 1
                    ps["groups"].add(gid)
                history_all.append({**h, "gid": gid, "group_display": display})
            full = data.get("history", [])
            if full:
                ts = int(full[-1].get("ts", 0))
                if last_ts is None or ts > last_ts:
                    last_ts = ts
        history_all.sort(key=lambda x: int(x.get("ts", 0)), reverse=True)
        # 显示名取该 QQ 最近一局的名字
        seen: dict[str, str] = {}
        for h in history_all:
            wq = str(h.get("winner", {}).get("qq", ""))
            if wq and wq in player_stats and wq not in seen:
                seen[wq] = h["winner"].get("name", "")
        for qq, ps in player_stats.items():
            if qq in seen:
                ps["name"] = seen[qq]
        ranking = sorted(player_stats.items(), key=lambda kv: -kv[1]["count"])[:10]
        return json_response({
            "range": range_key,
            "groups_count": len(gids),
            "groups_with_play": groups_with_play,
            "player_count": len(players_qq),
            "plays_in_range": total_plays,
            "last_active_ts": last_ts,
            "ranking": [{"qq": qq, "name": ps["name"], "play_count": ps["count"],
                         "groups_count": len(ps["groups"])} for qq, ps in ranking],
            "history": history_all[:20],
        })

    async def overview_sparkline(self):
        """全群每日局数（各群按天求和）。"""
        days = request.query.get("days", 7, type=int)
        days = max(1, min(days, 30))
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        counts: dict[datetime, int] = {}
        for gid in store.list_all_groups():
            data = store.load_group(gid)
            for h in data.get("history", []):
                d = datetime.fromtimestamp(int(h.get("ts", 0))).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                counts[d] = counts.get(d, 0) + 1
        points = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            points.append({"date": d.strftime("%m-%d"), "count": counts.get(d, 0)})
        return json_response({"points": points})

    async def set_alias(self, gid: str):
        payload = await request.json(default={})
        alias = str(payload.get("alias", "")).strip()
        data = store.load_group(gid)
        if alias:
            data["alias"] = alias
        else:
            data.pop("alias", None)
        store.save_group(gid, data)
        return json_response({"ok": True, "alias": alias})

    async def reset_group(self, gid: str):
        data = store.load_group(gid)
        data["players"] = {}
        store.save_group(gid, data)
        return json_response({"ok": True})

    async def delete_group(self, gid: str):
        store.unregister_group(gid)
        return json_response({"ok": True})
