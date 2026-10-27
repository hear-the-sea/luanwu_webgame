"""地图节点与赛季时间规则，不在读取时推进持久状态。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

NODES: dict[str, dict[str, Any]] = {
    "mountain:inn": {
        "name": "山脚驿站",
        "kind": "pve",
        "unlock_day": 1,
        "enemy_label": "山道劫匪",
        "description": "护送商队穿过山道，击退劫匪，积累功勋与补给。",
    },
    "mountain:gate": {
        "name": "青石关",
        "kind": "pvp",
        "unlock_day": 1,
        "enemy_label": "敌方阵营驻军",
        "description": "挑战实力相近的敌方阵容，每日对同一玩家最多进攻一次。",
    },
    "mountain:ruins": {
        "name": "古代遗迹",
        "kind": "pve",
        "unlock_day": 1,
        "enemy_label": "遗迹守卫",
        "description": "探索废弃城寨，挑战逐步增强的守卫。",
    },
    "river:town": {
        "name": "河口古城",
        "kind": "pvp",
        "unlock_day": 15,
        "enemy_label": "敌方城防队伍",
        "description": "参与古城争夺，积累阵营周度战果。",
    },
    "river:docks": {
        "name": "渡口码头",
        "kind": "pve",
        "unlock_day": 15,
        "enemy_label": "河口水匪",
        "description": "护送运粮船入港，击退水匪，为阵营筹备补给。",
    },
    "river:market": {
        "name": "沿岸集市",
        "kind": "shop",
        "unlock_day": 15,
        "enemy_label": "",
        "description": "使用个人赛季币兑换本阵营的装备、技能书和养成物品。",
    },
    "river:warehouse": {
        "name": "港口仓库",
        "kind": "supply",
        "unlock_day": 15,
        "enemy_label": "",
        "description": "交付赛季行动中获得的补给，推进个人战备里程碑。",
    },
    "forest:camp": {
        "name": "林间营寨",
        "kind": "lineup",
        "unlock_day": 36,
        "enemy_label": "",
        "description": "调整最多三名门客的赛季阵容，供出战与离线防守使用。",
    },
    "forest:granary": {
        "name": "粮仓与农田",
        "kind": "pve",
        "unlock_day": 36,
        "enemy_label": "粮道袭扰队",
        "description": "保护秋粮入仓，为后续行动积累补给。",
    },
    "forest:lodge": {
        "name": "林间驿舍",
        "kind": "pve",
        "unlock_day": 36,
        "enemy_label": "林间伏兵",
        "description": "探查林间道路，清除伏兵，获得探索功勋。",
    },
}
FACTION_NAMES = {"fiction": "小说阵营", "history": "历史阵营"}


def season_state(season: Any, now: datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    elapsed = (now - season.starts_at).total_seconds()
    day = max(0, int(elapsed // 86400) + 1) if elapsed >= 0 else 0
    phase = (
        "集结开拓"
        if day <= 14
        else "城港争夺" if day <= 35 else "三地会战" if day <= 70 else "决战" if day <= 83 else "赛季尾声"
    )
    if now < season.starts_at:
        phase = "等待开启"
    elif now >= season.ends_at or season.closed:
        phase = "已结束"
    return {
        "day": min(day, 90),
        "week": min(12, max(1, (max(day, 1) - 1) // 7 + 1)),
        "phase": phase,
        "combat_open": season.starts_at <= now < min(season.ends_at, season.starts_at + timedelta(days=83))
        and not season.closed,
        "days_left": max(0, int((season.ends_at - now).total_seconds() + 86399) // 86400),
    }


def front_node(week: int) -> str:
    if week <= 2:
        return "mountain:gate"
    return ["river:town", "mountain:gate"][(week - 3) % 2]
