from __future__ import annotations

from django.db import models


class BotProfile(models.Model):
    """System-controlled virtual player profile attached to a real Manor row."""

    class Archetype(models.TextChoices):
        BALANCED = "balanced", "均衡型"
        RICH = "rich", "肥羊型"
        DOJO = "dojo", "武馆型"
        GUARD = "guard", "护院型"
        ABANDONED = "abandoned", "弃坑型"

    class State(models.TextChoices):
        ACTIVE = "active", "正常成长"
        SLOWING = "slowing", "成长放缓"
        ABANDONED = "abandoned", "弃坑"
        STALE = "stale", "停滞"
        RETIRED = "retired", "退场"

    manor = models.OneToOneField(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="bot_profile",
        verbose_name="庄园",
    )
    archetype = models.CharField("类型", max_length=16, choices=Archetype.choices, default=Archetype.BALANCED)
    state = models.CharField("状态", max_length=16, choices=State.choices, default=State.ACTIVE)
    prestige_band = models.CharField("声望段", max_length=32, db_index=True)
    target_prestige_band = models.CharField("目标声望段", max_length=32, db_index=True, default="")
    current_prestige_band = models.CharField("当前声望段", max_length=32, db_index=True, default="")
    growth_seed = models.PositiveIntegerField("成长种子")
    growth_stage = models.PositiveSmallIntegerField("成长阶段", default=1)
    next_growth_at = models.DateTimeField("下次成长时间", db_index=True)
    abandon_at = models.DateTimeField("弃坑时间", db_index=True)
    retire_at = models.DateTimeField("退场时间", db_index=True)
    loot_budget_daily = models.PositiveIntegerField("每日资源预算", default=0)
    inventory_template_keys = models.JSONField("库存模板池", default=list, blank=True)
    maintenance_started_at = models.DateTimeField("维护开始时间", null=True, blank=True)
    maintenance_stopped_at = models.DateTimeField("维护停止时间", null=True, blank=True)
    last_planned_at = models.DateTimeField("最近规划时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家档案"
        verbose_name_plural = "虚拟玩家档案"
        indexes = [
            models.Index(fields=["state", "next_growth_at"], name="bot_state_next_growth_idx"),
            models.Index(fields=["prestige_band", "state"], name="bot_band_state_idx"),
            models.Index(fields=["target_prestige_band", "state"], name="bot_target_band_state_idx"),
            models.Index(fields=["current_prestige_band", "state"], name="bot_current_band_state_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.display_name} ({self.archetype}/{self.state})"


class BotInventoryDailyCounter(models.Model):
    """Daily global inventory budget consumed by virtual player projections."""

    category = models.CharField("类别", max_length=32)
    counter_date = models.DateField("计数日期")
    quantity = models.PositiveIntegerField("数量", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家每日库存计数"
        verbose_name_plural = "虚拟玩家每日库存计数"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "counter_date"],
                name="bot_inventory_daily_counter_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["counter_date", "category"], name="bot_inv_counter_day_cat_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.counter_date}:{self.category}={self.quantity}"


class BotBackfillDemand(models.Model):
    """Aggregated map/scout demand for later virtual player population rolls."""

    region = models.CharField("地区", max_length=32)
    prestige_band = models.CharField("声望段", max_length=32)
    needed = models.PositiveIntegerField("需求数量", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家补量需求"
        verbose_name_plural = "虚拟玩家补量需求"
        constraints = [
            models.UniqueConstraint(
                fields=["region", "prestige_band"],
                name="bot_backfill_demand_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["region", "prestige_band"], name="bot_backfill_region_band_idx"),
            models.Index(fields=["updated_at"], name="bot_backfill_updated_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.region}:{self.prestige_band} needs {self.needed}"
