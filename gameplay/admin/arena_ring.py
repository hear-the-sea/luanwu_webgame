from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.admin_i18n import apply_common_field_labels
from core.admin_records import ReadOnlyAdmin
from gameplay.models import ArenaRing, ArenaRingDaily, ArenaRingInspection, ArenaRingMatch, ArenaRingTenure

# 后台只读查询，不直接改写擂台占领关系、结算和奖励。
for model, name in (
    (ArenaRing, "擂台"),
    (ArenaRingTenure, "擂台占领记录"),
    (ArenaRingMatch, "擂台挑战记录"),
    (ArenaRingDaily, "擂台每日奖励统计"),
    (ArenaRingInspection, "擂台侦察记录"),
):
    model._meta.verbose_name = name
    model._meta.verbose_name_plural = name

apply_common_field_labels(
    ArenaRing,
    ArenaRingTenure,
    ArenaRingMatch,
    ArenaRingDaily,
    ArenaRingInspection,
    labels={
        "current_tenure": "当前占领记录",
        "version": "版本",
        "ring": "擂台",
        "lineup": "阵容快照",
        "settled_at": "最近结算时间",
        "end_reason": "结束原因",
        "defenses": "成功守擂次数",
        "lineup_updated_at": "阵容更新时间",
        "tenure": "占领记录",
        "pair_low": "对战双方较小编号",
        "pair_high": "对战双方较大编号",
        "reward_date": "奖励日期",
        "date": "日期",
        "seconds": "占领秒数",
        "time_coins": "占领奖励角斗币",
        "defense_rewards": "守擂奖励次数",
        "participation_rewards": "参与奖励次数",
        "challenges": "挑战次数",
        "buyer": "侦察玩家",
    },
)


class TenureStatusFilter(admin.SimpleListFilter):
    title = "占领状态"
    parameter_name = "tenure_status"

    def lookups(self, request, model_admin):
        return (("active", "占领中"), ("expired", "到期待结算"), ("ended", "已结束"))

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "active":
            return queryset.filter(ended_at__isnull=True, ends_at__gt=now)
        if self.value() == "expired":
            return queryset.filter(ended_at__isnull=True, ends_at__lte=now)
        if self.value() == "ended":
            return queryset.filter(ended_at__isnull=False)
        return queryset


class RingInspectionInline(admin.TabularInline):
    model = ArenaRingInspection
    fields = ("buyer", "version", "created_at")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("buyer__user")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ArenaRing)
class ArenaRingAdmin(ReadOnlyAdmin):
    list_display = ("name", "key", "holder", "deadline", "version")
    search_fields = ("name", "key", "current_tenure__manor__name", "current_tenure__manor__user__username")
    list_select_related = ("current_tenure__manor__user",)

    @admin.display(description="当前擂主")
    def holder(self, obj):
        return obj.current_tenure.manor if obj.current_tenure_id else "空擂"

    @admin.display(description="占领截止时间", ordering="current_tenure__ends_at")
    def deadline(self, obj):
        return obj.current_tenure.ends_at if obj.current_tenure_id else None


@admin.register(ArenaRingTenure)
class ArenaRingTenureAdmin(ReadOnlyAdmin):
    list_display = ("id", "ring", "manor", "state", "started_at", "ends_at", "defenses", "ended_at", "reason")
    list_filter = ("ring", TenureStatusFilter, "started_at")
    search_fields = ("manor__name", "manor__user__username", "ring__name", "=id")
    list_select_related = ("ring", "manor__user")
    date_hierarchy = "started_at"
    ordering = ("-started_at", "-pk")
    inlines = (RingInspectionInline,)

    def get_readonly_fields(self, request, obj=None):
        return tuple(field for field in super().get_readonly_fields(request, obj) if field != "end_reason") + (
            "state",
            "reason",
        )

    exclude = ("end_reason",)

    @admin.display(description="占领状态")
    def state(self, obj):
        if obj.ended_at:
            return "已结束"
        return "占领中" if obj.ends_at > timezone.now() else "到期待结算"

    @admin.display(description="结束原因")
    def reason(self, obj):
        return {"leave": "主动下台", "defeated": "挑战落败", "expired": "占领到期", "admin_reset": "运营重置"}.get(
            obj.end_reason, "未结束" if not obj.end_reason else "其他原因"
        )


@admin.register(ArenaRingMatch)
class ArenaRingMatchAdmin(ReadOnlyAdmin):
    list_display = ("id", "tenure", "attacker", "defender", "winner", "report_link", "reward_date", "created_at")
    list_filter = ("tenure__ring", "reward_date")
    search_fields = (
        "attacker__name",
        "attacker__user__username",
        "defender__name",
        "defender__user__username",
        "=battle_report__id",
    )
    list_select_related = ("tenure__ring", "attacker__user", "defender__user", "winner__user", "battle_report")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-pk")

    @admin.display(description="战报", ordering="battle_report_id")
    def report_link(self, obj):
        if not obj.battle_report_id:
            return "暂无战报"
        return format_html(
            '<a href="{}">战报 #{}</a>',
            reverse("admin:battle_battlereport_change", args=[obj.battle_report_id]),
            obj.battle_report_id,
        )


@admin.register(ArenaRingDaily)
class ArenaRingDailyAdmin(ReadOnlyAdmin):
    list_display = ("manor", "date", "seconds", "time_coins", "defense_rewards", "participation_rewards", "challenges")
    list_filter = ("date",)
    search_fields = ("manor__name", "manor__user__username")
    list_select_related = ("manor__user",)
    date_hierarchy = "date"
    ordering = ("-date", "-pk")
