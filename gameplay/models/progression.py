from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils import timezone


class PlayerTechnology(models.Model):
    """玩家技术等级"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="technologies")
    tech_key = models.CharField("技术标识", max_length=64)
    level = models.PositiveIntegerField("等级", default=0)
    is_upgrading = models.BooleanField("升级中", default=False)
    upgrade_complete_at = models.DateTimeField("升级完成时间", null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "玩家技术"
        verbose_name_plural = "玩家技术"
        unique_together = ("manor", "tech_key")
        indexes = [
            models.Index(fields=["manor", "is_upgrading", "upgrade_complete_at"], name="tech_upgrade_idx"),
            models.Index(fields=["is_upgrading", "upgrade_complete_at", "id"], name="tech_due_scan_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.tech_key} Lv{self.level}"

    def upgrade_duration(self) -> int:
        """
        计算升级到下一级所需的时间（秒）。

        公式: base_time * (1.4 ^ level)
        """
        from core.utils.time_scale import scale_duration
        from gameplay.services.technology import get_technology_template

        template = get_technology_template(self.tech_key)
        base_time = template.get("base_time", 60) if template else 60
        raw = base_time * (1.4**self.level)
        return scale_duration(raw, minimum=1)

    @property
    def time_remaining(self) -> int:
        """剩余升级时间（秒）"""
        if not self.upgrade_complete_at:
            return 0
        delta = self.upgrade_complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class WorkTemplate(models.Model):
    """工作地点模板"""

    class Tier(models.TextChoices):
        JUNIOR = "junior", "初级工作区"
        INTERMEDIATE = "intermediate", "中级工作区"
        SENIOR = "senior", "高级工作区"

    key = models.SlugField(unique=True, verbose_name="工作标识")
    name = models.CharField(max_length=64, verbose_name="工作名称")
    description = models.TextField(blank=True, verbose_name="工作简介")
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.JUNIOR, verbose_name="工作区等级")

    # 工作要求
    required_level = models.PositiveIntegerField(default=1, verbose_name="等级要求")
    required_force = models.PositiveIntegerField(default=0, verbose_name="武力要求")
    required_intellect = models.PositiveIntegerField(default=0, verbose_name="智力要求")
    required_defense = models.PositiveIntegerField(default=0, verbose_name="防御要求")
    required_agility = models.PositiveIntegerField(default=0, verbose_name="敏捷要求")

    # 工作报酬
    reward_silver = models.PositiveIntegerField(default=0, verbose_name="银两报酬")

    # 工作时长（秒）
    work_duration = models.PositiveIntegerField(default=7200, verbose_name="工作时长")

    # 显示顺序
    display_order = models.PositiveIntegerField(default=0, verbose_name="显示顺序")

    # 图标
    icon = models.CharField(max_length=32, blank=True, verbose_name="图标")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "工作模板"
        verbose_name_plural = "工作模板"
        ordering = ["tier", "display_order", "required_level"]

    def __str__(self) -> str:
        return f"{self.name}（{self.get_tier_display()}）"


class WorkAssignment(models.Model):
    """门客打工记录"""

    class Status(models.TextChoices):
        WORKING = "working", "打工中"
        COMPLETED = "completed", "已完成"
        RECALLED = "recalled", "已召回"

    manor = models.ForeignKey(
        "gameplay.Manor", on_delete=models.CASCADE, related_name="work_assignments", verbose_name="庄园"
    )
    guest = models.ForeignKey(
        "guests.Guest", on_delete=models.CASCADE, related_name="work_assignments", verbose_name="门客"
    )
    work_template = models.ForeignKey(
        WorkTemplate, on_delete=models.CASCADE, related_name="assignments", verbose_name="工作地点"
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.WORKING, verbose_name="状态")

    # 时间记录
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="开始时间")
    complete_at = models.DateTimeField(verbose_name="完成时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="实际完成时间")

    # 报酬记录
    reward_claimed = models.BooleanField(default=False, verbose_name="已领取报酬")

    class Meta:
        verbose_name = "打工记录"
        verbose_name_plural = "打工记录"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "-started_at"]),
            models.Index(fields=["guest", "-started_at"]),
            models.Index(fields=["manor", "status", "complete_at"], name="work_manor_sts_comp_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["manor", "work_template"],
                condition=Q(status="working"),
                name="uniq_working_assignment_per_work",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.guest.display_name} - {self.work_template.name}"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.WORKING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class PlayerTroop(models.Model):
    """玩家护院存储"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="troops")
    troop_template = models.ForeignKey(
        "battle.TroopTemplate",
        on_delete=models.CASCADE,
        related_name="player_troops",
        verbose_name="兵种模板",
    )
    count = models.PositiveIntegerField("数量", default=0)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "玩家护院"
        verbose_name_plural = "玩家护院"
        unique_together = ("manor", "troop_template")

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.troop_template.name} x{self.count}"


class TroopBankStorage(models.Model):
    """钱庄护院存储"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="troop_bank_storages")
    troop_template = models.ForeignKey(
        "battle.TroopTemplate",
        on_delete=models.CASCADE,
        related_name="troop_bank_storages",
        verbose_name="兵种模板",
    )
    count = models.PositiveIntegerField("数量", default=0)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "钱庄护院"
        verbose_name_plural = "钱庄护院"
        unique_together = ("manor", "troop_template")

    def __str__(self) -> str:
        return f"{self.manor.user.username} - 钱庄{self.troop_template.name} x{self.count}"


class HorseProduction(models.Model):
    """马匹生产队列"""

    class Status(models.TextChoices):
        PRODUCING = "producing", "生产中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="horse_productions")
    horse_key = models.CharField("马匹key", max_length=64)
    horse_name = models.CharField("马匹名称", max_length=64)
    quantity = models.PositiveIntegerField("生产数量", default=1)
    grain_cost = models.PositiveIntegerField("粮食消耗")
    base_duration = models.PositiveIntegerField("基础时长(秒)")
    actual_duration = models.PositiveIntegerField("实际时长(秒)")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PRODUCING)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("完成时间")
    finished_at = models.DateTimeField("实际完成时间", null=True, blank=True)

    class Meta:
        verbose_name = "马匹生产"
        verbose_name_plural = "马匹生产"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "complete_at"], name="horse_status_complete_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.horse_name} ({self.status})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.PRODUCING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class LivestockProduction(models.Model):
    """家畜养殖队列"""

    class Status(models.TextChoices):
        PRODUCING = "producing", "养殖中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="livestock_productions")
    livestock_key = models.CharField("家畜key", max_length=64)
    livestock_name = models.CharField("家畜名称", max_length=64)
    quantity = models.PositiveIntegerField("养殖数量", default=1)
    grain_cost = models.PositiveIntegerField("粮食消耗")
    base_duration = models.PositiveIntegerField("基础时长(秒)")
    actual_duration = models.PositiveIntegerField("实际时长(秒)")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PRODUCING)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("完成时间")
    finished_at = models.DateTimeField("实际完成时间", null=True, blank=True)

    class Meta:
        verbose_name = "家畜养殖"
        verbose_name_plural = "家畜养殖"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "complete_at"], name="livestock_status_complete_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.livestock_name} ({self.status})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.PRODUCING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class SmeltingProduction(models.Model):
    """金属冶炼队列"""

    class Status(models.TextChoices):
        PRODUCING = "producing", "冶炼中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="smelting_productions")
    metal_key = models.CharField("金属key", max_length=64)
    metal_name = models.CharField("金属名称", max_length=64)
    quantity = models.PositiveIntegerField("冶炼数量", default=1)
    cost_type = models.CharField("消耗类型", max_length=32, default="silver")
    cost_amount = models.PositiveIntegerField("消耗数量", default=0)
    base_duration = models.PositiveIntegerField("基础时长(秒)")
    actual_duration = models.PositiveIntegerField("实际时长(秒)")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PRODUCING)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("完成时间")
    finished_at = models.DateTimeField("实际完成时间", null=True, blank=True)

    class Meta:
        verbose_name = "金属冶炼"
        verbose_name_plural = "金属冶炼"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "complete_at"], name="smelting_status_complete_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.metal_name} ({self.status})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.PRODUCING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class EquipmentProduction(models.Model):
    """装备锻造队列"""

    class Status(models.TextChoices):
        FORGING = "forging", "锻造中"
        COMPLETED = "completed", "已完成"
        CANCELLED = "cancelled", "已取消"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="equipment_productions")
    equipment_key = models.CharField("装备key", max_length=64)
    equipment_name = models.CharField("装备名称", max_length=64)
    quantity = models.PositiveIntegerField("锻造数量", default=1)
    material_costs = models.JSONField("材料消耗", default=dict)
    base_duration = models.PositiveIntegerField("基础时长(秒)")
    actual_duration = models.PositiveIntegerField("实际时长(秒)")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.FORGING)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("完成时间")
    finished_at = models.DateTimeField("实际完成时间", null=True, blank=True)

    class Meta:
        verbose_name = "装备锻造"
        verbose_name_plural = "装备锻造"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "complete_at"], name="equipment_status_complete_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.equipment_name} ({self.status})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.FORGING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class TroopRecruitment(models.Model):
    """护院募兵队列"""

    class Status(models.TextChoices):
        RECRUITING = "recruiting", "募兵中"
        COMPLETED = "completed", "已完成"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="troop_recruitments")
    troop_key = models.CharField("兵种key", max_length=64)
    troop_name = models.CharField("兵种名称", max_length=64)
    quantity = models.PositiveIntegerField("募兵数量", default=1)
    equipment_costs = models.JSONField("装备消耗", default=dict)
    retainer_cost = models.PositiveIntegerField("家丁消耗", default=1)
    base_duration = models.PositiveIntegerField("基础时长(秒)")
    actual_duration = models.PositiveIntegerField("实际时长(秒)")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECRUITING)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("完成时间")
    finished_at = models.DateTimeField("实际完成时间", null=True, blank=True)

    class Meta:
        verbose_name = "护院募兵"
        verbose_name_plural = "护院募兵"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "complete_at"], name="troop_status_complete_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["manor"],
                condition=Q(status="recruiting"),
                name="uniq_active_troop_recruitment_per_manor",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.manor.user.username} - {self.troop_name} x{self.quantity} ({self.status})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.RECRUITING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))
