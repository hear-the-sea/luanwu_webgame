from __future__ import annotations

import math
from typing import Dict

from django.conf import settings
from django.db import models
from django.utils import timezone

from common.constants.resources import ResourceType

from ..constants import BuildingKeys

# ============ 庄园容量常量 ============

# 门客容量：基础值 + 等级 × 每级增量（0级基础容量3，满级15级共18位）
GUEST_CAPACITY_BASE = 3
GUEST_CAPACITY_PER_LEVEL = 1

# 家丁容量：基础值 + 等级 × 每级增量（0级就有基础容量）
RETAINER_CAPACITY_BASE = 50
RETAINER_CAPACITY_PER_LEVEL = 100

# 出战上限：基础值 + 等级 × 每级增量，封顶值
SQUAD_SIZE_BASE = 3
SQUAD_SIZE_PER_LEVEL = 1
SQUAD_SIZE_MAX = 18

# 训练速度：10级满级提升30%，每级约3.33%
TRAINING_SPEED_BONUS_PER_LEVEL = 0.0333

# 制造速度：10级满级提升50%，每级约5.56%
PRODUCTION_SPEED_BONUS_PER_LEVEL = 0.0556

# 祠堂：5级满级，建造及募兵速度提升25%（时间减少20%）
CITANG_BUILDING_TIME_REDUCTION_PER_LEVEL = 0.05  # 每级减少5%建筑时间
CITANG_RECRUITMENT_SPEED_BONUS_PER_LEVEL = 0.0625  # 每级提升6.25%招募速度

ACTION_POINTS_MAX = 1000


class Manor(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manor")
    name = models.CharField("庄园名称", max_length=20, unique=True, blank=True, null=True)
    grain = models.PositiveIntegerField("粮食", default=1200)
    silver = models.PositiveIntegerField("银两", default=5000)
    arena_coins = models.PositiveIntegerField("角斗币", default=0)
    arena_participations_today = models.PositiveSmallIntegerField("竞技场今日参与次数", default=0)
    arena_participation_date = models.DateField(
        "竞技场参与次数日期", null=True, blank=True, db_index=True, default=None
    )
    arena_coop_participations_today = models.PositiveSmallIntegerField("竞技场共斗今日参与次数", default=0)
    arena_coop_participation_date = models.DateField(
        "竞技场共斗参与次数日期", null=True, blank=True, db_index=True, default=None
    )
    storage_capacity = models.PositiveIntegerField("仓储上限", default=20000)
    silver_capacity = models.PositiveIntegerField("银库上限", default=20000)
    grain_capacity = models.PositiveIntegerField("粮仓上限", default=20000)
    retainer_count = models.PositiveIntegerField("家丁", default=0)
    prestige = models.PositiveIntegerField("声望", default=0)
    prestige_silver_spent = models.PositiveIntegerField("累计花费银两（声望计算用）", default=0)
    action_points = models.PositiveSmallIntegerField("行动力", default=ACTION_POINTS_MAX)
    action_points_updated_at = models.DateTimeField("行动力更新时间", default=timezone.now)
    resource_updated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    # ============ 庄园位置系统 ============
    region = models.CharField(
        "地区",
        max_length=32,
        default="overseas",
        db_index=True,
        help_text="庄园所在地区编码",
    )
    coordinate_x = models.PositiveIntegerField("X坐标", default=0)
    coordinate_y = models.PositiveIntegerField("Y坐标", default=0)
    occupied_region = models.GeneratedField(
        expression=models.Case(
            models.When(coordinate_x__gt=0, coordinate_y__gt=0, then=models.F("region")),
            default=models.Value(None),
        ),
        output_field=models.CharField(max_length=32, null=True),
        db_persist=True,
        editable=False,
        verbose_name="已占用坐标地区",
    )
    last_active_at = models.DateTimeField("最后活跃时间", default=timezone.now)

    # ============ 保护机制 ============
    newbie_protection_until = models.DateTimeField(
        "新手保护截止时间",
        null=True,
        blank=True,
        help_text="注册后7天内免受攻击",
    )
    defeat_protection_until = models.DateTimeField(
        "战败保护截止时间",
        null=True,
        blank=True,
        help_text="被踢馆失败后30分钟保护",
    )
    peace_shield_until = models.DateTimeField(
        "免战牌保护截止时间",
        null=True,
        blank=True,
        help_text="使用免战牌后的保护时间",
    )
    initial_peace_shield_granted_at = models.DateTimeField(
        "初始免战牌发放时间",
        null=True,
        blank=True,
        help_text="记录新手初始免战牌的发放时间，确保初始化失败后可安全重试且不会重复补发。",
    )
    last_relocation_at = models.DateTimeField(
        "上次迁移时间",
        null=True,
        blank=True,
        help_text="庄园迁移30天冷却",
    )

    class Meta:
        verbose_name = "庄园"
        verbose_name_plural = "庄园"
        constraints = [
            models.UniqueConstraint(
                fields=["occupied_region", "coordinate_x", "coordinate_y"],
                name="unique_occupied_manor_location",
            )
        ]
        indexes = [
            models.Index(fields=["region", "coordinate_x", "coordinate_y"]),
            models.Index(fields=["prestige"]),
        ]

    def __str__(self) -> str:
        return self.name or f"{self.user.username}的庄园"

    @property
    def display_name(self) -> str:
        """返回显示用的庄园名称"""
        return self.name or f"{self.user.username}的庄园"

    @property
    def region_display(self) -> str:
        """返回地区显示名称"""
        from ..constants import REGION_DICT

        return REGION_DICT.get(self.region, self.region)

    @property
    def location_display(self) -> str:
        """返回完整位置显示，如：[浙江] (156, 423)"""
        return f"[{self.region_display}] ({self.coordinate_x}, {self.coordinate_y})"

    @property
    def is_under_newbie_protection(self) -> bool:
        """是否处于新手保护期"""
        if not self.newbie_protection_until:
            return False
        return self.newbie_protection_until > timezone.now()

    @property
    def is_under_defeat_protection(self) -> bool:
        """是否处于战败保护期"""
        if not self.defeat_protection_until:
            return False
        return self.defeat_protection_until > timezone.now()

    @property
    def is_under_peace_shield(self) -> bool:
        """是否处于免战牌保护期"""
        if not self.peace_shield_until:
            return False
        return self.peace_shield_until > timezone.now()

    @property
    def is_protected(self) -> bool:
        """是否处于任何保护状态"""
        return self.is_under_newbie_protection or self.is_under_peace_shield or self.is_under_defeat_protection

    @property
    def can_relocate(self) -> bool:
        """是否可以迁移庄园"""
        from datetime import timedelta

        from ..constants import PVPConstants

        if self.is_under_newbie_protection:
            return False
        if self.last_relocation_at:
            cooldown_end = self.last_relocation_at + timedelta(days=PVPConstants.RELOCATION_COOLDOWN_DAYS)
            if cooldown_end > timezone.now():
                return False
        return True

    def resource_dict(self) -> Dict[str, int]:
        return {field: getattr(self, field) for field in ResourceType.values}

    # ============ 建筑等级缓存（优化 N+1 查询）============
    # 这些 property 会频繁访问，使用实例级缓存避免重复查询
    # 缓存在请求结束后自动失效（实例被回收）

    def _get_building_levels_cache(self) -> Dict[str, int]:
        """
        获取建筑等级缓存，一次查询加载所有关键建筑等级。

        优化前：每个 property 独立查询（4次查询）
        优化后：首次访问时批量查询，后续使用缓存（1次查询）
        """
        if not hasattr(self, "_building_levels"):
            # 批量查询所有关键建筑
            key_buildings = [
                BuildingKeys.JUXIAN_ZHUANG,
                BuildingKeys.JIADING_FANG,
                BuildingKeys.YOUXIA_BAOTA,
                BuildingKeys.LIANGGONG_CHANG,
                BuildingKeys.TREASURY,
                BuildingKeys.BATHHOUSE,
                BuildingKeys.SILVER_VAULT,
                BuildingKeys.GRANARY,
                BuildingKeys.RANCH,
                BuildingKeys.SMITHY,
                BuildingKeys.STABLE,
                BuildingKeys.TAVERN,
                BuildingKeys.CITANG,
                BuildingKeys.JAIL,
                BuildingKeys.OATH_GROVE,
                BuildingKeys.WALL,
                BuildingKeys.ARROW_TOWER,
            ]
            buildings = self.buildings.select_related("building_type").filter(building_type__key__in=key_buildings)
            self._building_levels = {b.building_type.key: b.level for b in buildings}
        return self._building_levels

    def invalidate_building_cache(self) -> None:
        """
        使建筑等级缓存失效。

        在建筑升级完成后调用此方法，确保下次访问时重新加载。
        """
        if hasattr(self, "_building_levels"):
            del self._building_levels

    def get_building_level(self, building_key: str) -> int:
        """获取指定建筑的等级，使用缓存"""
        cache = self._get_building_levels_cache()
        return cache.get(building_key, 1)

    @property
    def guest_capacity(self) -> int:
        """门客容量：基础值 + 聚贤庄等级 × 每级增量（0级3位，满级15级18位）"""
        level = self.get_building_level(BuildingKeys.JUXIAN_ZHUANG)
        return GUEST_CAPACITY_BASE + level * GUEST_CAPACITY_PER_LEVEL

    @property
    def retainer_capacity(self) -> int:
        """家丁容量：基础值 + 家丁房等级 × 每级增量（0级就有50个基础位置）"""
        level = self.get_building_level(BuildingKeys.JIADING_FANG)
        return RETAINER_CAPACITY_BASE + level * RETAINER_CAPACITY_PER_LEVEL

    @property
    def max_squad_size(self) -> int:
        """
        出战上限：基础值 + 游侠宝塔等级 × 每级增量，封顶 SQUAD_SIZE_MAX。
        0级3人，每级+1人，15级满级18人。
        """
        level = self.get_building_level(BuildingKeys.YOUXIA_BAOTA)
        return min(SQUAD_SIZE_MAX, SQUAD_SIZE_BASE + level * SQUAD_SIZE_PER_LEVEL)

    @property
    def guard_training_speed_multiplier(self) -> float:
        """
        练功场训练速度加成，每级增加 TRAINING_SPEED_BONUS_PER_LEVEL。
        """
        level = self.get_building_level(BuildingKeys.LIANGGONG_CHANG)
        return 1.0 + max(0, level - 1) * TRAINING_SPEED_BONUS_PER_LEVEL

    @property
    def hp_recovery_multiplier(self) -> float:
        """
        澡堂生命恢复加成，满级(20级)提供200%加成。
        每级增加10%，即 level * 0.10。
        1级=10%, 20级=200%
        """
        level = self.get_building_level(BuildingKeys.BATHHOUSE)
        return 1.0 + level * 0.10

    @property
    def calculated_silver_capacity(self) -> int:
        """
        银库容量：根据银库建筑等级计算。
        1级: 20,000  30级: 40,000,000
        公式: base * (growth ^ (level - 1))
        growth ≈ 1.299657
        """
        level = self.get_building_level(BuildingKeys.SILVER_VAULT)
        base = 20000
        growth = 1.299657
        return int(base * (growth ** (level - 1)))

    @property
    def calculated_grain_capacity(self) -> int:
        """
        粮仓容量：根据粮仓建筑等级计算。
        1级: 20,000  20级: 10,500,000
        公式: base * (growth ^ (level - 1))
        growth ≈ 1.3905
        """
        level = self.get_building_level(BuildingKeys.GRANARY)
        base = 20000
        growth = 1.3905
        return int(base * (growth ** (level - 1)))

    @property
    def livestock_production_multiplier(self) -> float:
        """
        畜牧场家畜制造速度加成。
        10级满级提升50%，每级约5.56%。
        """
        level = self.get_building_level(BuildingKeys.RANCH)
        return 1.0 + max(0, level - 1) * PRODUCTION_SPEED_BONUS_PER_LEVEL

    @property
    def smithy_production_multiplier(self) -> float:
        """
        冶炼坊物资制造速度加成。
        10级满级提升50%，每级约5.56%。
        """
        level = self.get_building_level(BuildingKeys.SMITHY)
        return 1.0 + max(0, level - 1) * PRODUCTION_SPEED_BONUS_PER_LEVEL

    @property
    def stable_production_multiplier(self) -> float:
        """
        马房马匹制造速度加成。
        10级满级提升50%，每级约5.56%。
        """
        level = self.get_building_level(BuildingKeys.STABLE)
        return 1.0 + max(0, level - 1) * PRODUCTION_SPEED_BONUS_PER_LEVEL

    @property
    def tavern_recruitment_bonus(self) -> int:
        """
        酒馆招募候选人数加成。
        10级满级增加10位候选人，每级增加1位。
        """
        level = self.get_building_level(BuildingKeys.TAVERN)
        return level

    @property
    def citang_building_time_reduction(self) -> float:
        """
        祠堂建筑升级时间减少比例。
        5级满级减少20%，每级减少5%。
        返回值为减少比例（如 0.2 表示减少20%，实际时间为原来的80%）。
        """
        level = self.get_building_level(BuildingKeys.CITANG)
        return max(0, level - 1) * CITANG_BUILDING_TIME_REDUCTION_PER_LEVEL

    @property
    def citang_recruitment_speed_multiplier(self) -> float:
        """
        祠堂护院招募速度加成倍率。
        5级满级提升25%，每级提升6.25%。
        返回值为速度倍率（如 1.25 表示速度为原来的125%，时间缩短为原来的80%）。
        """
        level = self.get_building_level(BuildingKeys.CITANG)
        return 1.0 + max(0, level - 1) * CITANG_RECRUITMENT_SPEED_BONUS_PER_LEVEL

    @property
    def jail_capacity(self) -> int:
        """监牢容量：等于监牢建筑等级（满级5）。"""
        level = self.get_building_level(BuildingKeys.JAIL)
        return max(0, min(5, int(level)))

    @property
    def oath_capacity(self) -> int:
        """结义人数上限：等于结义林建筑等级（满级5）。"""
        level = self.get_building_level(BuildingKeys.OATH_GROVE)
        return max(0, min(5, int(level)))


class BuildingCategory(models.TextChoices):
    RESOURCE = "resource", "资源生产"
    STORAGE = "storage", "仓储设施"
    PRODUCTION = "production", "生产加工"
    PERSONNEL = "personnel", "人员管理"
    SPECIAL = "special", "特殊建筑"
    CITY_DEFENSE = "city_defense", "城防建筑"


class BuildingType(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField("建筑名称", max_length=64)
    description = models.TextField(blank=True)
    category = models.CharField(
        "建筑分类",
        max_length=16,
        choices=BuildingCategory.choices,
        default=BuildingCategory.RESOURCE,
    )
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    base_rate_per_hour = models.PositiveIntegerField("一级产量(每小时)", default=50)
    rate_growth = models.FloatField("每级增长系数", default=0.15)
    base_upgrade_time = models.PositiveIntegerField("一级建造时间(秒)", default=60)
    time_growth = models.FloatField("时间成长系数", default=1.25)
    base_cost = models.JSONField(default=dict)
    cost_growth = models.FloatField("成本成长系数", default=1.35)
    icon = models.CharField(max_length=32, blank=True)

    class Meta:
        verbose_name = "建筑类型"
        verbose_name_plural = "建筑类型"

    def __str__(self) -> str:
        return self.name


class Building(models.Model):
    manor = models.ForeignKey(Manor, on_delete=models.CASCADE, related_name="buildings")
    building_type = models.ForeignKey(BuildingType, on_delete=models.CASCADE, related_name="instances")
    level = models.PositiveIntegerField(default=1)
    is_upgrading = models.BooleanField(default=False)
    upgrade_complete_at = models.DateTimeField(null=True, blank=True)
    current_hp = models.PositiveIntegerField("当前耐久", default=0)
    hp_updated_at = models.DateTimeField("耐久恢复时间", default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "建筑"
        verbose_name_plural = "建筑"
        unique_together = ("manor", "building_type")
        indexes = [
            models.Index(fields=["manor", "is_upgrading", "upgrade_complete_at"], name="building_upgrade_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username}-{self.building_type.name}-Lv{self.level}"

    def hourly_rate(self) -> float:
        growth = 1 + self.building_type.rate_growth * (self.level - 1)
        return self.building_type.base_rate_per_hour * growth

    def next_level_cost(self) -> Dict[str, int]:
        target_level = self.level + 1
        multiplier = self.building_type.cost_growth ** (target_level - 1)
        return {
            resource: math.ceil(amount * multiplier)
            for resource, amount in (self.building_type.base_cost or {}).items()
        }

    def next_level_duration(self) -> int:
        target_level = self.level + 1
        multiplier = self.building_type.time_growth ** (target_level - 1)
        return math.ceil(self.building_type.base_upgrade_time * multiplier)
