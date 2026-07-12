from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.config import GUEST

from . import guest_rules as _guest_rules

if TYPE_CHECKING:
    from .managers import GuestManager as GuestManagerType

GENDER_CHOICES = [
    ("male", "男"),
    ("female", "女"),
    ("unknown", "未知"),
]


class GuestRarity(models.TextChoices):
    """稀有度枚举（从低到高排列）"""

    BLACK = "black", "黑"
    GRAY = "gray", "灰"
    GREEN = "green", "绿"
    RED = "red", "红"
    BLUE = "blue", "蓝"
    PURPLE = "purple", "紫"
    ORANGE = "orange", "橙"


class GuestArchetype(models.TextChoices):
    CIVIL = "civil", "文"
    MILITARY = "military", "武"


class GuestStatus(models.TextChoices):
    IDLE = "idle", "空闲"
    WORKING = "working", "打工中"
    DEPLOYED = "deployed", "出征中"
    ARENA = "arena", "竞技中"
    INJURED = "injured", "重伤"


# 成长率配置已移除，改用直接数值成长
# 属性分配逻辑见 guests/utils/attribute_growth.py


class GuestTemplate(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    archetype = models.CharField(max_length=16, choices=GuestArchetype.choices)
    rarity = models.CharField(max_length=16, choices=GuestRarity.choices)
    base_attack = models.PositiveIntegerField(default=100)
    base_intellect = models.PositiveIntegerField(default=100)
    base_defense = models.PositiveIntegerField(default=100)
    base_agility = models.PositiveIntegerField(default=80)
    base_luck = models.PositiveIntegerField(default=50)
    base_hp = models.PositiveIntegerField(default=1200)
    avatar = models.ImageField(upload_to="guests/", blank=True, null=True, verbose_name="门客头像")
    flavor = models.CharField(max_length=512, blank=True)
    default_gender = models.CharField(max_length=16, choices=GENDER_CHOICES, default="unknown")
    default_morality = models.PositiveIntegerField(default=50)
    initial_skills: models.ManyToManyField["Skill", "Skill"] = models.ManyToManyField(
        "Skill",
        blank=True,
        related_name="template_initials",
    )
    recruitable = models.BooleanField(default=True)
    # 是否为隐士（隐藏在民间的高手，虽为黑色但不可重复招募）
    is_hermit = models.BooleanField(default=False, verbose_name="隐士")
    # 自定义成长点数区间 [min, max]，为空则使用稀有度默认值
    growth_range = models.JSONField(default=list, blank=True, verbose_name="成长点数区间")
    # 自定义属性分配权重 {force: X, intellect: Y, defense: Z, agility: W}
    attribute_weights = models.JSONField(default=dict, blank=True, verbose_name="属性分配权重")

    class Meta:
        verbose_name = "门客模板"
        verbose_name_plural = "门客模板"

    def __str__(self) -> str:
        return self.name


class RecruitmentPool(models.Model):
    class Tier(models.TextChoices):
        CUNMU = "cunmu", "村募"
        XIANGSHI = "xiangshi", "乡试"
        HUISHI = "huishi", "会试"
        DIANSHI = "dianshi", "殿试"

    key = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    cost = models.JSONField(default=dict)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.CUNMU)
    draw_count = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = "招募卡池"
        verbose_name_plural = "招募卡池"

    def __str__(self) -> str:
        return self.name


class RecruitmentPoolEntry(models.Model):
    pool = models.ForeignKey(RecruitmentPool, on_delete=models.CASCADE, related_name="entries")
    template = models.ForeignKey(GuestTemplate, on_delete=models.CASCADE, null=True, blank=True)
    rarity = models.CharField(max_length=16, choices=GuestRarity.choices, blank=True, null=True)
    archetype = models.CharField(max_length=16, choices=GuestArchetype.choices, blank=True, null=True)
    weight = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = "卡池配置"
        verbose_name_plural = "卡池配置"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(template__isnull=False) | models.Q(rarity__isnull=False),
                name="pool_entry_template_or_category",
            )
        ]


class SkillKind(models.TextChoices):
    ACTIVE = "active", "主动"
    PASSIVE = "passive", "被动"


class Skill(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    rarity = models.CharField(max_length=16, choices=GuestRarity.choices, default=GuestRarity.GRAY)
    description = models.TextField(blank=True)
    base_power = models.PositiveIntegerField(default=100)
    base_probability = models.FloatField(default=0.1)
    kind = models.CharField(max_length=16, choices=SkillKind.choices, default=SkillKind.ACTIVE)
    status_effect = models.CharField(max_length=32, blank=True)
    status_probability = models.FloatField(default=0.0)
    status_duration = models.PositiveIntegerField(default=1)
    damage_formula = models.JSONField(default=dict, blank=True)
    passive_config = models.JSONField(default=dict, blank=True)
    required_level = models.PositiveIntegerField(default=0)
    required_force = models.PositiveIntegerField(default=0)
    required_intellect = models.PositiveIntegerField(default=0)
    required_defense = models.PositiveIntegerField(default=0)
    required_agility = models.PositiveIntegerField(default=0)
    targets = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = "门客技能"
        verbose_name_plural = "门客技能"

    def __str__(self) -> str:
        return self.name


class SkillBook(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="books")
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "技能书"
        verbose_name_plural = "技能书"

    def __str__(self) -> str:
        return self.name


class Guest(models.Model):
    # 使用自定义 Manager 提供便捷查询方法
    from .managers import GuestManager

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="guests")
    template = models.ForeignKey(GuestTemplate, on_delete=models.CASCADE)
    level = models.PositiveIntegerField(default=1)
    experience = models.PositiveIntegerField(default=0)
    attack_bonus = models.IntegerField(default=0)
    defense_bonus = models.IntegerField(default=0)
    custom_name = models.CharField("自定义称号", max_length=64, blank=True)
    force = models.PositiveIntegerField("武力", default=80)
    intellect = models.PositiveIntegerField("智力", default=80)
    defense_stat = models.PositiveIntegerField("防御", default=80)
    agility = models.PositiveIntegerField("敏捷", default=80)
    luck = models.PositiveIntegerField("运势", default=50)
    loyalty = models.PositiveIntegerField("忠诚度", default=80)
    loyalty_processed_for_date = models.DateField(
        "忠诚度处理日期",
        null=True,
        blank=True,
        db_index=True,
        help_text="记录最近一次每日忠诚度结算的日期，用于避免重复执行",
    )
    hp_bonus = models.IntegerField(default=0)
    troop_capacity_bonus = models.IntegerField(default=0)
    current_hp = models.PositiveIntegerField(default=0)
    last_hp_recovery_at = models.DateTimeField(default=timezone.now)
    gear_set_bonus = models.JSONField(default=dict, blank=True)
    attribute_points = models.PositiveIntegerField("属性点", default=0)

    # 招募时的初始属性（含浮动），用于计算升级成长
    initial_force = models.PositiveIntegerField("初始武力", default=0)
    initial_intellect = models.PositiveIntegerField("初始智力", default=0)
    initial_defense = models.PositiveIntegerField("初始防御", default=0)
    initial_agility = models.PositiveIntegerField("初始敏捷", default=0)

    # 玩家手动分配的属性点数
    allocated_force = models.PositiveIntegerField("已分配武力", default=0)
    allocated_intellect = models.PositiveIntegerField("已分配智力", default=0)
    allocated_defense = models.PositiveIntegerField("已分配防御", default=0)
    allocated_agility = models.PositiveIntegerField("已分配敏捷", default=0)

    # 洗髓丹使用次数（每个门客最多使用10次，重生后重置）
    xisuidan_used = models.PositiveIntegerField("洗髓丹已使用次数", default=0)

    gender = models.CharField(max_length=16, choices=GENDER_CHOICES, default="unknown")
    morality = models.PositiveIntegerField("品性", default=50)
    status = models.CharField(max_length=16, choices=GuestStatus.choices, default=GuestStatus.IDLE)
    skills: models.ManyToManyField["Skill", "Skill"] = models.ManyToManyField(
        Skill,
        through="GuestSkill",
        blank=True,
    )
    training_target_level = models.PositiveIntegerField(default=0)
    training_complete_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects: "GuestManagerType" = GuestManager()

    class Meta:
        verbose_name = "门客"
        verbose_name_plural = "门客"
        indexes = [
            models.Index(fields=["manor", "status"], name="guest_manor_status_idx"),
            models.Index(fields=["manor", "-created_at"], name="guest_manor_created_idx"),
            models.Index(fields=["training_complete_at"], name="guest_training_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.display_name} Lv{self.level}"

    @property
    def display_name(self) -> str:
        return self.custom_name or self.template.name

    @property
    def rarity(self) -> str:
        return self.template.rarity

    @property
    def archetype(self) -> str:
        return self.template.archetype

    @property
    def is_idle(self) -> bool:
        return self.status == GuestStatus.IDLE

    @property
    def max_hp(self) -> int:
        return _guest_rules.compute_guest_max_hp(self)

    def restore_full_hp(self) -> None:
        update_fields = _guest_rules.restore_guest_full_hp(
            self,
            injured_status=GuestStatus.INJURED,
            idle_status=GuestStatus.IDLE,
        )
        self.save(update_fields=update_fields)

    # 成长倍率相关方法已移除，改用直接数值成长
    # 升级时直接增加门客的实际属性值

    def stat_block(self) -> Dict[str, int]:
        return _guest_rules.build_guest_stat_block(self)

    @property
    def troop_capacity(self) -> int:
        return _guest_rules.compute_guest_troop_capacity(self)

    def _initialize_current_hp_if_missing(self) -> None:
        if self._state.adding and int(getattr(self, "current_hp", 0) or 0) <= 0:
            self.current_hp = self.max_hp

    def save(self, *args, **kwargs):
        self._initialize_current_hp_if_missing()
        super().save(*args, **kwargs)


class GearSlot(models.TextChoices):
    HELMET = "helmet", "头盔"
    ARMOR = "armor", "衣甲"
    WEAPON = "weapon", "武器"
    SHOES = "shoes", "鞋子"
    DEVICE = "device", "器械"
    MOUNT = "mount", "坐骑"
    ORNAMENT = "ornament", "饰品"


class GearTemplate(models.Model):
    key = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    slot = models.CharField(max_length=16, choices=GearSlot.choices)
    rarity = models.CharField(max_length=16, choices=GuestRarity.choices, default=GuestRarity.GRAY)
    set_key = models.CharField(max_length=64, blank=True, default="")
    set_description = models.CharField(max_length=128, blank=True, default="")
    set_bonus = models.JSONField(default=dict, blank=True)
    attack_bonus = models.IntegerField(default=0)
    defense_bonus = models.IntegerField(default=0)
    extra_stats = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "装备模板"
        verbose_name_plural = "装备模板"

    def __str__(self) -> str:
        return self.name


class GearItem(models.Model):
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="gears")
    template = models.ForeignKey(GearTemplate, on_delete=models.CASCADE)
    guest = models.ForeignKey(Guest, on_delete=models.SET_NULL, null=True, blank=True, related_name="gear_items")
    inventory_backed = models.BooleanField(default=False)
    level = models.PositiveIntegerField(default=1)
    acquired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "装备实例"
        verbose_name_plural = "装备实例"
        indexes = [
            models.Index(fields=["manor", "guest"], name="gearitem_manor_guest_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.template.name} Lv{self.level}"


class RecruitmentRecord(models.Model):
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="recruit_records")
    pool = models.ForeignKey(RecruitmentPool, on_delete=models.SET_NULL, null=True)
    guest = models.ForeignKey(Guest, on_delete=models.CASCADE)
    rarity = models.CharField(max_length=16, choices=GuestRarity.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "招募记录"
        verbose_name_plural = "招募记录"
        ordering = ("-created_at",)


class GuestRecruitment(models.Model):
    """门客招募队列（异步倒计时）"""

    class Status(models.TextChoices):
        PENDING = "pending", "招募中"
        COMPLETED = "completed", "已完成"
        FAILED = "failed", "失败"

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="guest_recruitments")
    pool = models.ForeignKey(RecruitmentPool, on_delete=models.SET_NULL, null=True)
    cost = models.JSONField("招募消耗", default=dict)
    draw_count = models.PositiveIntegerField("候选数量", default=1)
    duration_seconds = models.PositiveIntegerField("招募时长(秒)", default=0)
    seed = models.BigIntegerField("随机种子", default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField("开始时间", auto_now_add=True)
    complete_at = models.DateTimeField("完成时间")
    finished_at = models.DateTimeField("实际完成时间", null=True, blank=True)
    result_count = models.PositiveIntegerField("实际生成候选", default=0)
    error_message = models.CharField("失败原因", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "门客招募队列"
        verbose_name_plural = "门客招募队列"
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["manor", "status", "complete_at"], name="guest_recruit_msc_idx"),
            models.Index(fields=["status", "complete_at"], name="guest_recruit_sc_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["manor"],
                condition=models.Q(status="pending"),
                name="uniq_pending_guest_recruitment_per_manor",
            ),
        ]

    def __str__(self) -> str:
        pool_name = self.pool.name if self.pool_id and self.pool else "未知卡池"
        return f"{self.manor.user.username} - {pool_name} ({self.status})"

    @property
    def time_remaining(self) -> int:
        """剩余时间（秒）"""
        if self.status != self.Status.PENDING:
            return 0
        delta = self.complete_at - timezone.now()
        return max(0, int(delta.total_seconds()))


class TrainingLog(models.Model):
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE)
    guest = models.ForeignKey(Guest, on_delete=models.CASCADE)
    delta_level = models.PositiveIntegerField()
    resource_cost = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "培养记录"
        verbose_name_plural = "培养记录"
        ordering = ("-created_at",)


class GuestSkill(models.Model):
    class Source(models.TextChoices):
        TEMPLATE = "template", "模板"
        BOOK = "book", "技能书"

    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="guest_skills")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.TEMPLATE)
    learned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "门客技能"
        verbose_name_plural = "门客技能"
        unique_together = ("guest", "skill")

    def clean(self):
        super().clean()
        self._ensure_capacity()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def _ensure_capacity(self):
        if not self.guest_id:
            return
        current = GuestSkill.objects.filter(guest_id=self.guest_id).exclude(pk=self.pk).count()
        if current >= int(GUEST.MAX_SKILL_SLOTS):
            raise ValidationError("技能位已满，无法继续学习新的技能。")


class RecruitmentCandidate(models.Model):
    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="candidates")
    pool = models.ForeignKey(RecruitmentPool, on_delete=models.CASCADE)
    template = models.ForeignKey(GuestTemplate, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=64)
    rarity = models.CharField(max_length=16, choices=GuestRarity.choices)
    archetype = models.CharField(max_length=16, choices=GuestArchetype.choices)
    rarity_revealed = models.BooleanField(default=False, verbose_name="稀有度已显示")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "招募候选"
        verbose_name_plural = "招募候选"
        ordering = ("created_at",)


class SalaryPayment(models.Model):
    """工资支付记录"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="salary_payments")
    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="salary_payments")
    amount = models.PositiveIntegerField("工资金额")
    paid_at = models.DateTimeField(auto_now_add=True, verbose_name="支付时间")
    for_date = models.DateField("支付日期", help_text="为哪一天支付的工资")

    class Meta:
        verbose_name = "工资支付记录"
        verbose_name_plural = "工资支付记录"
        ordering = ("-paid_at",)
        indexes = [
            models.Index(fields=["guest", "for_date"]),
            models.Index(fields=["manor", "for_date"]),
            models.Index(fields=["for_date"], name="guests_salar_for_date_idx"),
        ]
        unique_together = ("guest", "for_date")

    def __str__(self) -> str:
        return f"{self.guest.display_name} - {self.for_date} - {self.amount}银两"


class GuestDefection(models.Model):
    """门客叛逃记录"""

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="guest_defections")
    guest_id = models.PositiveBigIntegerField("原门客ID", unique=True, null=True, blank=True)
    guest_name = models.CharField(max_length=64, verbose_name="门客名称")
    guest_level = models.PositiveIntegerField("门客等级")
    guest_rarity = models.CharField(max_length=16, choices=GuestRarity.choices, verbose_name="稀有度")
    loyalty_at_defection = models.PositiveIntegerField("叛逃时忠诚度")
    defected_at = models.DateTimeField(auto_now_add=True, verbose_name="叛逃时间")

    class Meta:
        verbose_name = "门客叛逃记录"
        verbose_name_plural = "门客叛逃记录"
        ordering = ("-defected_at",)

    def __str__(self) -> str:
        return f"{self.guest_name} (Lv{self.guest_level}) 叛逃"
