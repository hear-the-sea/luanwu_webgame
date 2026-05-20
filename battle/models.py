from __future__ import annotations

from django.db import models
from django.utils import timezone


class TroopTemplate(models.Model):
    """兵种模板"""

    key = models.SlugField(unique=True, verbose_name="兵种标识")
    name = models.CharField(max_length=64, verbose_name="兵种名称")
    description = models.TextField(blank=True, verbose_name="描述")
    base_attack = models.PositiveIntegerField(default=30, verbose_name="基础攻击")
    base_defense = models.PositiveIntegerField(default=20, verbose_name="基础防御")
    base_hp = models.PositiveIntegerField(default=80, verbose_name="基础生命")
    speed_bonus = models.PositiveIntegerField(default=10, verbose_name="速度加成")
    priority = models.IntegerField(default=0, verbose_name="显示优先级")
    default_count = models.PositiveIntegerField(default=120, verbose_name="默认数量")
    avatar = models.ImageField(upload_to="troops/", blank=True, null=True, verbose_name="兵种形象")

    class Meta:
        verbose_name = "兵种模板"
        verbose_name_plural = "兵种模板"
        ordering = ["priority", "key"]

    def __str__(self) -> str:
        return self.name


class BattleReport(models.Model):
    RESULT_CHOICES = [
        ("attacker", "进攻方胜利"),
        ("defender", "防守方胜利"),
        ("draw", "平局"),
    ]

    manor = models.ForeignKey("gameplay.Manor", on_delete=models.CASCADE, related_name="battle_reports")
    opponent_name = models.CharField(max_length=64)
    battle_type = models.CharField(max_length=32, default="skirmish")
    attacker_team = models.JSONField(default=list)
    attacker_troops = models.JSONField(default=dict)
    attacker_city_defenses = models.JSONField(default=list)
    defender_team = models.JSONField(default=list)
    defender_troops = models.JSONField(default=dict)
    defender_city_defenses = models.JSONField(default=list)
    rounds = models.JSONField(default=list)
    losses = models.JSONField(default=dict)
    drops = models.JSONField(default=dict)
    winner = models.CharField(max_length=16, choices=RESULT_CHOICES)
    starts_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    seed = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "战报"
        verbose_name_plural = "战报"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["manor", "-created_at"], name="battlereport_manor_cr_idx"),
        ]

    @property
    def countdown_seconds(self) -> int:
        return max(0, int((self.starts_at - timezone.now()).total_seconds()))
