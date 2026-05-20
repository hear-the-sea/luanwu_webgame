"""
游戏核心配置

集中管理游戏中的各类常量配置，便于统一调整和维护。

使用示例:
    from core.config import GuestConfig, BattleConfig, TimeConfig

    max_level = GuestConfig.MAX_LEVEL
    hp_per_defense = GuestConfig.DEFENSE_TO_HP_MULTIPLIER
    max_rounds = BattleConfig.MAX_ROUNDS
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet

# ============ 时间配置 ============


@dataclass(frozen=True)
class TimeConfig:
    """时间相关配置（单位：秒）"""

    MINUTE: int = 60
    HOUR: int = 3600
    DAY: int = 86400

    # 生命值恢复
    HP_RECOVERY_INTERVAL: int = 600  # 10分钟更新一次
    HP_FULL_RECOVERY_TIME: int = 24 * 3600  # 24小时完全恢复

    # 资源更新
    RESOURCE_UPDATE_INTERVAL: int = 60  # 1分钟

    # 训练检查
    TRAINING_CHECK_INTERVAL: int = 60


# ============ 门客配置 ============


@dataclass(frozen=True)
class GuestConfig:
    """门客系统配置"""

    # 等级限制
    MAX_LEVEL: int = 100

    # 技能槽位
    MAX_SKILL_SLOTS: int = 3

    # 属性计算
    DEFENSE_TO_HP_MULTIPLIER: int = 50  # 每点防御提供的额外HP
    MIN_HP_FLOOR: int = 200  # 最低HP下限

    # 带兵容量
    BASE_TROOP_CAPACITY: int = 200  # 基础带兵数量
    BONUS_TROOP_CAPACITY: int = 50  # 满级额外带兵
    TROOP_CAPACITY_LEVEL_THRESHOLD: int = 70  # 获得额外带兵的等级门槛

    # 战斗属性权重
    CIVIL_FORCE_WEIGHT: float = 0.5  # 文官武力权重
    CIVIL_INTELLECT_WEIGHT: float = 0.5  # 文官智力权重
    MILITARY_FORCE_WEIGHT: float = 0.7  # 武将武力权重
    MILITARY_INTELLECT_WEIGHT: float = 0.3  # 武将智力权重

    # 重伤恢复阈值
    INJURY_RECOVERY_HP_PERCENT: float = 0.3  # 30% HP 解除重伤


# ============ 庄园配置 ============


@dataclass(frozen=True)
class ManorConfig:
    """庄园系统配置"""

    # 门客容量
    GUEST_CAPACITY_BASE: int = 3
    GUEST_CAPACITY_PER_LEVEL: int = 1

    # 家丁容量
    RETAINER_CAPACITY_BASE: int = 50
    RETAINER_CAPACITY_PER_LEVEL: int = 100

    # 出战上限
    SQUAD_SIZE_BASE: int = 3
    SQUAD_SIZE_PER_LEVEL: int = 1
    SQUAD_SIZE_MAX: int = 18

    # 训练速度
    TRAINING_SPEED_BONUS_PER_LEVEL: float = 0.03

    # 初始资源
    INITIAL_GRAIN: int = 1200
    INITIAL_SILVER: int = 500
    INITIAL_STORAGE_CAPACITY: int = 20000


# ============ 战斗配置 ============


@dataclass(frozen=True)
class BattleConfig:
    """战斗系统配置"""

    # 战斗限制
    MAX_SQUAD: int = 5
    MAX_ROUNDS: int = 32
    DEFAULT_BATTLE_TYPE: str = "skirmish"

    # 伤害计算
    COUNTER_DAMAGE_MULTIPLIER: float = 0.5  # 反击伤害倍率
    GUEST_VS_GUEST_DEFENSE_CONSTANT: float = 300.0  # 门客对门客防御常数

    # 暴击
    BASE_CRIT_CHANCE: float = 0.05  # 基础暴击率
    CRIT_DAMAGE_MULTIPLIER: float = 1.5  # 暴击伤害倍率


# ============ 建筑配置 ============


@dataclass(frozen=True)
class BuildingKeys:
    """建筑类型标识"""

    JUXIAN_ZHUANG: str = "juxianzhuang"  # 聚贤庄
    JIADING_FANG: str = "jiadingfang"  # 家丁房
    YOUXIA_BAOTA: str = "youxibaota"  # 游侠宝塔
    LIANBING_DAYING: str = "lianbingdaying"  # 练兵大营
    LIANGGONG_CHANG: str = "lianggongchang"  # 练功场
    TREASURY: str = "treasury"  # 藏宝阁
    BATHHOUSE: str = "bathhouse"  # 澡堂
    LATRINE: str = "latrine"  # 茅厕
    SILVER_VAULT: str = "silver_vault"  # 银库
    GRANARY: str = "granary"  # 粮仓
    RANCH: str = "ranch"  # 畜牧场
    SMITHY: str = "smithy"  # 冶炼坊
    STABLE: str = "stable"  # 马房
    TAVERN: str = "tavern"  # 酒馆
    FORGE: str = "forge"  # 铁匠铺
    CITANG: str = "citang"  # 祠堂
    JAIL: str = "jail"  # 监牢
    OATH_GROVE: str = "oath_grove"  # 结义林
    WALL: str = "wall"  # 城墙
    ARROW_TOWER: str = "arrow_tower"  # 箭塔


# ============ 稀有度配置 ============


@dataclass(frozen=True)
class RarityConfig:
    """稀有度相关配置"""

    # 稀有度顺序（从低到高）
    ORDER: tuple = ("black", "gray", "green", "blue", "red", "purple", "orange")

    # 稀有度基础HP
    HP_PROFILES: Dict[str, int] = field(default_factory=dict)  # 在 __post_init__ 中设置

    # 稀有度工资
    SALARY: Dict[str, int] = field(default_factory=dict)  # 在 __post_init__ 中设置

    def __post_init__(self):
        # 由于 frozen=True，需要通过 object.__setattr__ 设置
        object.__setattr__(
            self,
            "HP_PROFILES",
            {
                "black": 900,
                "gray": 1100,
                "green": 1400,
                "red": 1550,
                "blue": 1700,
                "purple": 2400,
                "orange": 2800,
            },
        )
        object.__setattr__(
            self,
            "SALARY",
            {
                "black": 500,
                "gray": 1000,
                "green": 2000,
                "red": 3000,
                "blue": 4000,
                "purple": 15000,
                "orange": 30000,
            },
        )


# ============ 装备槽位配置 ============


@dataclass(frozen=True)
class EquipmentConfig:
    """装备系统配置"""

    # 每个槽位的容量
    SLOT_CAPACITY: Dict[str, int] = field(default_factory=dict)

    # 可多装备的槽位
    MULTI_EQUIP_SLOTS: FrozenSet[str] = frozenset({"device", "ornament"})

    def __post_init__(self):
        object.__setattr__(
            self,
            "SLOT_CAPACITY",
            {
                "helmet": 1,
                "armor": 1,
                "weapon": 1,
                "shoes": 1,
                "mount": 1,
                "device": 3,
                "ornament": 3,
            },
        )


# ============ 招募配置 ============


@dataclass(frozen=True)
class RecruitmentConfig:
    """招募系统配置"""

    # 核心卡池
    CORE_POOL_TIERS: tuple = ("cunmu", "xiangshi", "huishi", "dianshi")

    # 候选过期时间
    CANDIDATE_EXPIRE_HOURS: int = 24

    # 每个卡池每日招募次数上限（按庄园独立统计）
    DAILY_POOL_DRAW_LIMIT: int = 300


# ============ 打工配置 ============


@dataclass(frozen=True)
class WorkConfig:
    """打工系统配置"""

    # 工作区时长（秒）
    JUNIOR_DURATION: int = 2 * 3600  # 2小时
    INTERMEDIATE_DURATION: int = 3 * 3600  # 3小时
    SENIOR_DURATION: int = 4 * 3600  # 4小时


# ============ 仓库配置 ============


@dataclass(frozen=True)
class WarehouseConfig:
    """仓库/道具相关配置"""

    # 门客升阶道具可使用的来源门客模板 key
    RARITY_UPGRADE_SOURCE_TEMPLATE_KEYS: FrozenSet[str] = frozenset(
        {
            "hist_sljnbc_0589",
            "hist_sljnbc_0590",
            "hist_sljnbc_0589_blue",
            "hist_sljnbc_0590_blue",
        }
    )


# ============ 技术配置 ============


@dataclass(frozen=True)
class TechnologyConfig:
    """技术系统配置"""

    # 升级时间计算
    BASE_UPGRADE_TIME: int = 60  # 基础60秒
    TIME_GROWTH_FACTOR: float = 1.4  # 时间成长系数

    # 最高等级
    MAX_LEVEL: int = 10


# ============ 门客忠诚度配置 ============


@dataclass(frozen=True)
class GuestLoyaltyConfig:
    """门客忠诚度系统配置"""

    # 叛逃相关
    DEFECTION_PROBABILITY: float = 0.3  # 低忠诚度门客每日叛逃概率 (30%)
    DEFECTION_BATCH_SIZE: int = 500  # 叛逃批量处理大小
    DEFECTION_QUERY_CHUNK_SIZE: int = 2000  # 叛逃候选查询分块大小


# ============ 安全配置 ============


@dataclass(frozen=True)
class SecurityConfig:
    """安全相关配置"""

    # 登录限制
    LOGIN_ATTEMPT_LIMIT: int = 5  # 最大尝试次数
    LOGIN_ATTEMPT_WINDOW: int = 300  # 时间窗口（秒）
    LOGIN_LOCKOUT_DURATION: int = 900  # 锁定时长（秒）


# ============ 消息配置 ============


@dataclass(frozen=True)
class MessageConfig:
    """消息系统配置"""

    RETENTION_DAYS: int = 7  # 消息保留天数


# ============ 交易配置 ============


@dataclass(frozen=True)
class TradeConfig:
    """交易系统配置"""

    # 市场交易
    TRANSACTION_TAX_RATE: float = 0.10  # 交易税率 10%
    MIN_PRICE_MULTIPLIER: float = 1.0  # 最低价格为物品price的1倍
    MAX_PRICE: int = 10000000  # 最高1000万银两
    MAX_TOTAL_PRICE: int = 2000000000  # 最高总价20亿（防止整数溢出）

    # 商店
    BUY_PRICE_MULTIPLIER: int = 2  # 购买价 = 基准价 * 2


# ============ 单例实例 ============

# 提供全局访问的配置实例
TIME = TimeConfig()
GUEST = GuestConfig()
MANOR = ManorConfig()
BATTLE = BattleConfig()
BUILDING_KEYS = BuildingKeys()
RARITY = RarityConfig()
EQUIPMENT = EquipmentConfig()
RECRUITMENT = RecruitmentConfig()
WORK = WorkConfig()
WAREHOUSE = WarehouseConfig()
TECHNOLOGY = TechnologyConfig()
GUEST_LOYALTY = GuestLoyaltyConfig()
SECURITY = SecurityConfig()
MESSAGE = MessageConfig()
TRADE = TradeConfig()
