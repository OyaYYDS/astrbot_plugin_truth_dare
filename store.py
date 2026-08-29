"""名单存储：按群 JSON 原子写 + 连中降权加权随机 + 运行时状态。

规则（设计文档 10.3/10.4/10.5/10.9）：
- 按群独立文件存储，QQ 号为唯一身份，显示名仅作展示
- JSON 原子写（临时文件 + os.replace）
- 连中降权：权重 = 1/(1+连中次数×系数)，恒 >0；未选中者每局连中计数-1
- 抽选状态与退出防刷为运行时状态（不落盘）
"""

import json
import os
import random
import time
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_data_path

DATA_DIR = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_truth_dare"
GROUPS_DIR = DATA_DIR / "groups"
REGISTRY_PATH = DATA_DIR / "groups_registry.json"

WARN_INTERVAL = 300.0  # 防刷：5 分钟内同类提醒只回一次
HISTORY_LIMIT = 200    # 每群历史记录滚动保留上限

_runtime: dict[str, dict] = {}


def _gstate(gid: str) -> dict:
    if gid not in _runtime:
        _runtime[gid] = {"spinning": False, "warns": {}}
    return _runtime[gid]


# ---------- 群数据 ----------

def group_path(gid: str) -> Path:
    return GROUPS_DIR / f"{gid}.json"


def load_group(gid: str) -> dict:
    p = group_path(gid)
    if not p.is_file():
        return {"enabled": True, "registered": False, "players": {}, "total_plays": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        data.setdefault("enabled", True)
        data.setdefault("registered", False)
        data.setdefault("players", {})
        data.setdefault("total_plays", 0)
        return data
    except Exception:
        # 设计文档 10.8：损坏不得静默覆盖，保留原文件，记录错误
        from astrbot.api import logger
        logger.error(f"[truth_dare] 群 {gid} 名单文件损坏，已跳过读取，原文件保留：{p}")
        return {"enabled": True, "registered": False, "players": {}, "total_plays": 0}


def save_group(gid: str, data: dict) -> None:
    GROUPS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = group_path(gid).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, group_path(gid))


def players_list(data: dict) -> list[tuple[str, dict]]:
    """按插入顺序返回 [(qq, info), ...]（渲染顺序与名单顺序一致）。"""
    return list(data.get("players", {}).items())


def find_player(data: dict, target: str) -> list[str]:
    """精确 QQ 号匹配 → 显示名匹配（可能多个）。"""
    target = (target or "").strip()
    if target in data.get("players", {}):
        return [target]
    return [qq for qq, info in data.get("players", {}).items() if info.get("name") == target]


# ---------- 连中降权 ----------

def weighted_pick(players: dict, coef: float) -> str:
    """加权随机：权重 = 1/(1+连中次数×系数)，恒 >0。"""
    items = list(players.items())
    weights = [1.0 / (1.0 + max(0, int(info.get("streak", 0))) * coef) for _, info in items]
    total = sum(weights)
    r = random.uniform(0, total)
    acc = 0.0
    for (qq, _), w in zip(items, weights):
        acc += w
        if r <= acc:
            return qq
    return items[-1][0]


def apply_result(data: dict, winner_qq: str, coef: float) -> None:
    """选中者连中+1、未选中者 max(0, 连中-1)；总游玩次数+1。"""
    for qq, info in data.get("players", {}).items():
        if qq == winner_qq:
            info["streak"] = int(info.get("streak", 0)) + 1
            info["play_count"] = int(info.get("play_count", 0)) + 1
        else:
            info["streak"] = max(0, int(info.get("streak", 0)) - 1)
    data["total_plays"] = int(data.get("total_plays", 0)) + 1


def list_all_groups() -> list[str]:
    """统计页用：所有玩过（有数据文件）或登记过的群，按群号排序。"""
    gids = set(load_registry())
    if GROUPS_DIR.is_dir():
        for p in GROUPS_DIR.glob("*.json"):
            gids.add(p.stem)
    return sorted(gids, key=lambda x: int(x) if x.isdigit() else 0)


# ---------- 群登记（统计预留，UI 之后做） ----------

def load_registry() -> list[str]:
    if not REGISTRY_PATH.is_file():
        return []
    try:
        return list(json.loads(REGISTRY_PATH.read_text(encoding="utf-8-sig")))
    except Exception:
        return []


def save_registry(ids: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def register_group(gid: str) -> None:
    """登记群（手动，用于严格模式预登记）。"""
    ids = load_registry()
    if gid not in ids:
        ids.append(gid)
        save_registry(ids)
    data = load_group(gid)
    data["registered"] = True
    save_group(gid, data)


def ensure_registered(gid: str) -> None:
    """自动登记：任何群第一次产生游戏数据时调用（玩过即登记）。"""
    if gid in load_registry():
        return
    register_group(gid)


def unregister_group(gid: str) -> None:
    """移除登记并清空该群全部数据（设计文档 11.4：群删除=清空）。"""
    ids = [x for x in load_registry() if x != gid]
    save_registry(ids)
    p = group_path(gid)
    try:
        os.remove(p)
    except OSError:
        pass


# ---------- 运行时状态 ----------

def is_spinning(gid: str) -> bool:
    return bool(_gstate(gid)["spinning"])


def mark_spinning(gid: str, spinning: bool) -> None:
    _gstate(gid)["spinning"] = spinning


def should_warn(gid: str, key: str) -> bool:
    """按 key 分类的防刷提醒：间隔内同类提醒只回一次，返回 False 表示静默。"""
    st = _gstate(gid)
    now = time.time()
    if now - st["warns"].get(key, 0.0) < WARN_INTERVAL:
        return False
    st["warns"][key] = now
    return True


def clear_warn(gid: str, key: str) -> None:
    """重置某类防刷状态（如用户加入名单后重置其退出防刷）。"""
    _gstate(gid)["warns"].pop(key, None)


def add_history(data: dict, snapshot: dict) -> None:
    """追加一局历史快照，滚动保留最近 HISTORY_LIMIT 局。"""
    history = data.setdefault("history", [])
    history.append(snapshot)
    if len(history) > HISTORY_LIMIT:
        del history[: len(history) - HISTORY_LIMIT]
