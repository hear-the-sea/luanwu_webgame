"""赛季专用科技：普通护院随建设成长，嗜血铁骑固定满级。"""

from core.game_data.cavalry import CAVALRY_BERSERK_MAX_LEVEL, CAVALRY_STAT_TECHS

CAVALRY_TECH_NAMES = {
    "wolf_force": "铁骑攻击",
    "wolf_defense": "铁骑防御",
    "wolf_hp": "铁骑生命",
    "wolf_agility": "铁骑速度",
    "wolf_berserk": "铁骑冲阵",
}


def season_technology_levels(levels: dict[str, int]) -> dict[str, int]:
    """在赛季战斗边界统一应用，兼容旧的在途科技快照。"""
    return {
        **levels,
        **{key: maximum for key, (_, _, maximum) in CAVALRY_STAT_TECHS.items()},
        "wolf_berserk": CAVALRY_BERSERK_MAX_LEVEL,
    }
