"""
战斗模拟常数配置
"""

from typing import Dict

# ============ 目标选择配置 ============

# 优先目标权重：60% 概率选择优先目标，40% 保留随机性
# 这个权重经过平衡性考虑：既能遏制炮灰策略，又保留足够的不确定性
PRIORITY_TARGET_WEIGHT = 0.60

# ============ 五行相克配置 ============

# 五行相克关系：护院攻击时优先选择克制兵种
# 克制关系：刀→剑→拳→弓→枪→刀
TROOP_COUNTERS: Dict[str, str] = {
    "dao": "jian",  # 刀克剑
    "jian": "quan",  # 剑克拳
    "quan": "gong",  # 拳克弓
    "gong": "qiang",  # 弓克枪
    "qiang": "dao",  # 枪克刀
}

# 克制伤害倍率：攻击被克制目标时的额外伤害
COUNTER_DAMAGE_MULTIPLIER = 1.5

# ============ 防御减伤常数配置 ============

# 标准防御常数（用于小兵战斗）
# 公式：damage_reduction = defense / (defense + constant)
# 值越大，相同防御下减伤越低
DEFAULT_DEFENSE_CONSTANT = 120

# 门客对门客防御常数（软上限公式）
# 公式：base = defense / (defense + 600)，超过50%后收益减半，上限75%
# 设计理由：
# - 常数600让防御属性更有价值（313防御34%减伤，803防御54%减伤）
# - 防御收益从+1.2回合提升到+2.2回合，装备有意义
# - 配合15x倍率保持合理战斗节奏（约5-7回合击杀）
GUEST_VS_GUEST_DEFENSE_CONSTANT = 600

# 小兵对门客防御常数（软上限公式的基础常数）
# 公式：base = defense / (defense + 200)，超过50%后收益减半，上限75%
# 效果：低防御有效，高防御收益递减，装备提升不会导致极端减伤
TROOP_VS_GUEST_DEFENSE_CONSTANT = 200

# 软上限阈值：超过此值后，额外减伤收益减半
SOFTCAP_THRESHOLD = 0.50

# 硬上限：减伤不会超过此值（适用于小兵打门客、小兵打小兵）
HARDCAP = 0.75

# 门客对门客额外伤害倍率
# 设计理由：
# - 配合常数600的软上限公式（34-54%减伤）
# - 15x倍率确保战斗在合理回合内结束
# - 313防御约5.2回合击杀，803防御约7.3回合击杀
GUEST_VS_GUEST_DAMAGE_MULTIPLIER = 15.0

# ============ 战斗数值常数 ============

# 基础暴击率（敏捷不再影响暴击）
BASE_CRIT_CHANCE = 0.05

# 暴击伤害倍率
CRIT_DAMAGE_MULTIPLIER = 1.5

# 先手伤害衰减比例（门客先手时的伤害降低）
PREEMPTIVE_DAMAGE_REDUCTION = 0.8

# 伤害随机波动范围
DAMAGE_VARIANCE_MIN = 0.9
DAMAGE_VARIANCE_MAX = 1.1

# 门客对小兵的防御减伤常量（渐进公式）
# 公式：reduction = defense / (defense + GUEST_VS_TROOP_DEFENSE_CONSTANT)
# K=50 设计效果（中等强度）：
#   - 防御4: 7.4% 减伤
#   - 防御6: 10.7% 减伤
#   - 防御10: 16.7% 减伤
#   - 防御13: 20.6% 减伤
# 高防兵种（枪系）有明显战略价值，低防兵种差距也不会太大
GUEST_VS_TROOP_DEFENSE_CONSTANT = 50

# 门客技能打护院的额外伤害倍率（普攻屠戮倍率独立计算）
# 设计目标：压制技能秒杀护院，但保留技能的明显收益
GUEST_SKILL_VS_TROOP_MULTIPLIER = 5.0

# ============ 城防伤害倍率 ============

GUEST_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER = 3.0
TROOP_VS_CITY_DEFENSE_DAMAGE_MULTIPLIER = 0.5
ARROW_TOWER_VS_GUEST_DAMAGE_MULTIPLIER = 3.0
ARROW_TOWER_VS_TROOP_DAMAGE_MULTIPLIER = 2.0

# ============ 优先级阶段配置 ============

# 允许的最小优先级值（防止配置错误导致无限循环）
MIN_ALLOWED_PRIORITY = -10

# 允许的最大优先级值
MAX_ALLOWED_PRIORITY = 10
