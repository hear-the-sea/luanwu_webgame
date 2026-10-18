"""嗜血铁骑的战斗科技规则，供战斗与科技展示共用。"""

CAVALRY_STAT_TECHS = {
    "wolf_force": ("attack", 0.10, 10),
    "wolf_defense": ("defense", 0.10, 10),
    "wolf_hp": ("hp", 0.10, 10),
    "wolf_agility": ("agility", 0.05, 10),
}
CAVALRY_BERSERK_MAX_LEVEL = 5
CAVALRY_PREEMPT_PRIORITY = -1
CAVALRY_BERSERK_CHANCE_PER_LEVEL = 0.10
CAVALRY_BERSERK_MULTI_DAMAGE_MULTIPLIER = 0.70
CAVALRY_BERSERK_SINGLE_DAMAGE_MULTIPLIER = 1.20
