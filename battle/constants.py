from typing import Dict

from core.config import BATTLE

# 从 core.config 导入配置，保持向后兼容
MAX_SQUAD = BATTLE.MAX_SQUAD
MAX_ROUNDS = BATTLE.MAX_ROUNDS
DEFAULT_BATTLE_TYPE = BATTLE.DEFAULT_BATTLE_TYPE

BATTLE_TYPES: Dict[str, dict] = {
    DEFAULT_BATTLE_TYPE: {
        "name": "乱军试炼",
        "description": "与乱军短兵相接，侦察敌情并掠夺补给",
        "loot_pool": {
            # Use literal resource keys to avoid importing gameplay at import-time (reduces app coupling).
            "grain": 300,
            "silver": 200,
        },
    },
    "task1": {
        "name": "任务战斗",
        "description": "任务华山论剑",
        "loot_pool": {},
    },
    "arena": {
        "name": "竞技场对战",
        "description": "玩家间竞技场比武",
        "loot_pool": {},
    },
    "arena_coop": {
        "name": "围攻光明顶",
        "description": "五人共斗，围攻光明顶上的明教群雄",
        "loot_pool": {},
        "max_rounds": 24,
    },
}


def get_battle_config(battle_type: str) -> dict:
    return BATTLE_TYPES.get(battle_type, BATTLE_TYPES[DEFAULT_BATTLE_TYPE])
