"""赛季持久状态；业务写入通过 season services 完成。"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

FACTIONS = [("fiction", "小说阵营"), ("history", "历史阵营")]


class Season(models.Model):
    name = models.CharField("赛季名称", max_length=64)
    starts_at = models.DateTimeField("开始时间", db_index=True)
    ends_at = models.DateTimeField("结束时间", db_index=True)
    rules = models.JSONField("赛季规则", default=dict)
    population = models.JSONField("人口统计", default=dict)
    population_updated_at = models.DateTimeField("人口统计时间", null=True, blank=True)
    closed = models.BooleanField("已结束", default=False, db_index=True)
    settled_weeks = models.PositiveSmallIntegerField("已结算周数", default=0)
    fiction_score = models.PositiveIntegerField("小说阵营战果", default=0)
    history_score = models.PositiveIntegerField("历史阵营战果", default=0)
    winner = models.CharField("胜方", max_length=16, choices=FACTIONS, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "赛季"
        verbose_name_plural = "赛季"
        ordering = ["-starts_at", "-id"]

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError("赛季结束时间必须晚于开始时间")


class SeasonMember(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="members", verbose_name="赛季")
    manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="season_memberships",
        verbose_name="庄园",
    )
    faction = models.CharField("阵营", max_length=16, choices=FACTIONS, blank=True)
    preference = models.CharField("意向", max_length=16, blank=True)
    lineup = models.JSONField("阵容", default=dict)
    strength = models.PositiveBigIntegerField("综合实力", default=0)
    prestige_snapshot = models.PositiveBigIntegerField("报名声望", default=0)
    pending_resources = models.JSONField("暂存物资", default=dict)
    pending_construction = models.PositiveBigIntegerField("暂存建设值", default=0)
    combat_merit = models.PositiveIntegerField("战斗贡献", default=0)
    logistics_merit = models.PositiveIntegerField("后勤贡献", default=0)
    troop_date = models.DateField("领兵日期", null=True, blank=True)
    troops_drawn = models.PositiveIntegerField("当日领兵数", default=0)
    final_rank = models.PositiveIntegerField("最终排名", null=True, blank=True)
    final_tier = models.CharField("奖励档位", max_length=24, blank=True)
    final_reward = models.JSONField("最终奖励", default=dict)
    merit = models.PositiveIntegerField("总贡献", default=0)
    coins = models.PositiveIntegerField("赛季钱币", default=0)
    supplies = models.PositiveIntegerField("补给", default=0)
    donated = models.PositiveIntegerField("已捐献", default=0)
    action_date = models.DateField("行动日期", null=True, blank=True)
    actions_used = models.PositiveSmallIntegerField("当日已用行动", default=0)
    scroll_available = models.BooleanField("卷轴可领", default=False)
    scroll_used = models.BooleanField("卷轴已用", default=False)
    joined_at = models.DateTimeField("报名时间", auto_now_add=True)

    class Meta:
        verbose_name = "赛季成员"
        verbose_name_plural = "赛季成员"
        constraints = [models.UniqueConstraint(fields=["season", "manor"], name="season_member_unique")]
        indexes = [models.Index(fields=["season", "faction"], name="season_member_faction_idx")]


class SeasonAction(models.Model):
    member = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="actions", verbose_name="赛季成员")
    token = models.UUIDField("操作凭证")
    request_hash = models.CharField("请求摘要", max_length=64)
    kind = models.CharField("行动类型", max_length=24)
    node_key = models.CharField("据点标识", max_length=40, blank=True)
    date = models.DateField("行动日期", db_index=True)
    week = models.PositiveSmallIntegerField("所属周", default=0)
    war_points = models.PositiveSmallIntegerField("战果", default=0)
    opponent = models.ForeignKey(
        SeasonMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="defenses",
        verbose_name="交战对手",
    )
    report = models.ForeignKey(
        "battle.BattleReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="战报",
    )
    result = models.JSONField("行动结果", default=dict)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "赛季行动"
        verbose_name_plural = "赛季行动"
        constraints = [models.UniqueConstraint(fields=["member", "token"], name="season_action_token_unique")]
        indexes = [models.Index(fields=["member", "week", "kind"], name="season_action_week_idx")]
        ordering = ["-created_at", "-id"]


class SeasonClaim(models.Model):
    member = models.ForeignKey(SeasonMember, on_delete=models.CASCADE, related_name="claims", verbose_name="赛季成员")
    key = models.CharField("领取标识", max_length=64)
    quantity = models.PositiveIntegerField("数量", default=1)
    created_at = models.DateTimeField("领取时间", auto_now_add=True)

    class Meta:
        verbose_name = "赛季领取"
        verbose_name_plural = "赛季领取"
        constraints = [models.UniqueConstraint(fields=["member", "key"], name="season_claim_unique")]


class SeasonWeek(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="weeks", verbose_name="赛季")
    number = models.PositiveSmallIntegerField("周数")
    node_key = models.CharField("争夺据点", max_length=40)
    fiction_points = models.PositiveIntegerField("小说阵营胜点", default=0)
    history_points = models.PositiveIntegerField("历史阵营胜点", default=0)
    matched_members = models.PositiveIntegerField("匹配人数", default=0)
    winner = models.CharField("周胜方", max_length=16, choices=FACTIONS, blank=True)
    settled_at = models.DateTimeField("结算时间")

    class Meta:
        verbose_name = "赛季周"
        verbose_name_plural = "赛季周"
        constraints = [models.UniqueConstraint(fields=["season", "number"], name="season_week_unique")]
        ordering = ["number"]


class SeasonFaction(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="faction_states", verbose_name="赛季")
    faction = models.CharField("阵营", max_length=16, choices=FACTIONS)
    resources = models.JSONField("公共资源", default=dict)
    troops = models.JSONField("阵营护院", default=dict)
    levels = models.JSONField("设施等级", default=dict)
    technology = models.JSONField("护院科技", default=dict)
    produced_at = models.DateTimeField("产出结算时间")
    construction_points = models.PositiveBigIntegerField("建设值", default=0)
    economy_state = models.JSONField("经济状态", default=dict)
    score = models.PositiveIntegerField("累计战果", default=0)

    class Meta:
        verbose_name = "阵营状态"
        verbose_name_plural = "阵营状态"
        constraints = [models.UniqueConstraint(fields=["season", "faction"], name="season_faction_unique")]


class SeasonSite(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="sites", verbose_name="赛季")
    key = models.CharField("据点标识", max_length=40)
    owner = models.CharField("占领方", max_length=16, choices=FACTIONS, blank=True)
    guards = models.PositiveIntegerField("守军", default=0)
    durability = models.PositiveIntegerField("城防", default=0)
    control = models.PositiveIntegerField("控制值", default=0)
    attacker = models.CharField("进攻方", max_length=16, choices=FACTIONS, blank=True)
    protected_until = models.DateTimeField("保护至", null=True, blank=True)
    last_score_at = models.DateTimeField("最近计分时间")
    front_line = models.JSONField("前线配置", default=dict)
    front_allocations = models.JSONField("前线分配", default=dict)
    reinforcement_at = models.DateTimeField("增援时间", null=True, blank=True)
    supplies = models.JSONField("补给", default=dict)
    supply_updated_at = models.DateTimeField("补给更新时间", null=True, blank=True)
    capture_cycle = models.PositiveIntegerField("占领轮次", default=0)
    npc_profile = models.JSONField("守军配置", default=dict)
    defenses = models.JSONField("防御配置", default=dict)

    class Meta:
        verbose_name = "赛季据点"
        verbose_name_plural = "赛季据点"
        constraints = [models.UniqueConstraint(fields=["season", "key"], name="season_site_unique")]


class SeasonIntel(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="intel_records", verbose_name="赛季")
    member = models.ForeignKey(
        SeasonMember,
        on_delete=models.CASCADE,
        related_name="intel_records",
        verbose_name="赛季成员",
    )
    faction = models.CharField("阵营", max_length=16, choices=FACTIONS, blank=True)
    node_key = models.CharField("据点标识", max_length=40)
    kind = models.CharField("情报类型", max_length=24)
    payload = models.JSONField("情报内容", default=dict)
    expires_at = models.DateTimeField("有效期至")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "赛季情报"
        verbose_name_plural = "赛季情报"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["member", "node_key", "expires_at"], name="season_intel_member_idx"),
            models.Index(fields=["season", "faction", "expires_at"], name="season_intel_faction_idx"),
        ]


class SeasonRally(models.Model):
    KIND_CHOICES = [("attack", "集结进攻"), ("siege", "集结破城")]
    STATUS_CHOICES = [("gathering", "集结中"), ("marching", "行军中"), ("resolved", "已结算"), ("cancelled", "已取消")]
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="rallies", verbose_name="赛季")
    leader = models.ForeignKey(
        SeasonMember, on_delete=models.CASCADE, related_name="led_rallies", verbose_name="发起人"
    )
    node_key = models.CharField("集结据点", max_length=40)
    kind = models.CharField("集结类型", max_length=16, choices=KIND_CHOICES)
    status = models.CharField("状态", max_length=16, choices=STATUS_CHOICES, default="gathering")
    gathers_until = models.DateTimeField("集结截止")
    launched_at = models.DateTimeField("出发时间", null=True, blank=True)
    due_at = models.DateTimeField("抵达时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    result = models.JSONField("集结结果", default=dict)

    class Meta:
        verbose_name = "赛季集结"
        verbose_name_plural = "赛季集结"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "gathers_until"], name="season_rally_gather_idx"),
            models.Index(fields=["status", "due_at"], name="season_rally_due_idx"),
        ]


class SeasonExpedition(models.Model):
    STATUS_CHOICES = [
        (key, label)
        for key, label in (
            ("gathering", "集结中"),
            ("outbound", "行军中"),
            ("stationed", "驻防中"),
            ("transporting", "运输中"),
            ("returning", "返程中"),
            ("completed", "已归队"),
            ("failed", "异常待处理"),
        )
    ]
    member = models.ForeignKey(
        SeasonMember,
        on_delete=models.CASCADE,
        related_name="expeditions",
        verbose_name="赛季成员",
    )
    rally = models.ForeignKey(
        SeasonRally,
        on_delete=models.SET_NULL,
        related_name="expeditions",
        null=True,
        blank=True,
        verbose_name="所属集结",
    )
    node_key = models.CharField("目标据点", max_length=40)
    kind = models.CharField("队伍类型", max_length=24)
    status = models.CharField(
        "状态",
        max_length=24,
        choices=STATUS_CHOICES,
        default="outbound",
        db_index=True,
    )
    guest_ids = models.JSONField("门客快照", default=list)
    guests = models.ManyToManyField(
        "guests.Guest", related_name="season_expeditions", blank=True, verbose_name="出征门客"
    )
    lineup = models.JSONField("阵容", default=dict)
    troops = models.JSONField("携带护院", default=dict)
    initial_troops = models.JSONField("初始护院", default=dict)
    technology = models.JSONField("护院科技", default=dict)
    cargo = models.JSONField("货物", default=dict)
    intercepted = models.BooleanField("已被拦截", default=False)
    battles = models.PositiveSmallIntegerField("战斗次数", default=0)
    seed = models.PositiveIntegerField("随机种子", default=0)
    travel_seconds = models.PositiveIntegerField("行军耗时", default=0)
    launched_at = models.DateTimeField("出发时间")
    due_at = models.DateTimeField("到期时间", db_index=True)
    stationed_until = models.DateTimeField("驻防至", null=True, blank=True)
    report = models.ForeignKey(
        "battle.BattleReport",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="战报",
    )
    result = models.JSONField("队伍结果", default=dict)
    error = models.CharField("异常信息", max_length=256, blank=True)

    class Meta:
        verbose_name = "赛季队伍"
        verbose_name_plural = "赛季队伍"
        ordering = ["-launched_at", "-id"]
        indexes = [models.Index(fields=["status", "due_at"], name="season_exp_due_idx")]
