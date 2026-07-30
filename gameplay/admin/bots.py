from datetime import UTC, datetime

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.urls import path
from django.utils import timezone
from django.views.decorators.http import require_POST

from gameplay.services.virtual_player_core.contracts import (
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
)
from gameplay.services.virtual_player_core.maintenance import V2MaintenanceError, maintain_virtual_player_v2
from gameplay.services.virtual_player_core.profile_store import mark_profiles_stale

from ..models import (
    BotBackfillDemand,
    BotExternalStrengthReconciliation,
    BotInventoryDailyCounter,
    BotPolicyRelease,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
)


class DueMaintenanceFilter(admin.SimpleListFilter):
    title = "待维护"
    parameter_name = "due_maintenance"

    def lookups(self, request, model_admin):
        return (
            ("yes", "待维护"),
            ("no", "未到期"),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "yes":
            return queryset.exclude(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED]).filter(
                next_growth_at__lte=now
            )
        if self.value() == "no":
            return queryset.filter(
                Q(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED]) | Q(next_growth_at__gt=now)
            )
        return queryset


def _single_admin_maintenance_parameter(request: HttpRequest, name: str) -> str:
    values = request.POST.getlist(name)
    if len(values) != 1:
        raise ValueError(f"{name} must be provided exactly once")
    return values[0]


def _parse_admin_maintenance_parameters(
    request: HttpRequest,
) -> tuple[int, bool, MaintenanceScheduleDisposition]:
    allowed_fields = {
        "csrfmiddlewaretoken",
        "profile_id",
        "requires_due",
        "schedule_disposition",
    }
    unknown_fields = sorted(set(request.POST) - allowed_fields)
    if unknown_fields:
        raise ValueError(f"unknown parameters: {', '.join(unknown_fields)}")
    if "csrfmiddlewaretoken" in request.POST and len(request.POST.getlist("csrfmiddlewaretoken")) != 1:
        raise ValueError("csrfmiddlewaretoken must be provided at most once")

    raw_profile_id = _single_admin_maintenance_parameter(request, "profile_id")
    if not raw_profile_id.isascii() or not raw_profile_id.isdecimal():
        raise ValueError("profile_id must be a positive integer")
    profile_id = int(raw_profile_id)
    if profile_id < 1:
        raise ValueError("profile_id must be a positive integer")

    raw_requires_due = _single_admin_maintenance_parameter(request, "requires_due")
    try:
        requires_due = {"true": True, "false": False}[raw_requires_due]
    except KeyError as exc:
        raise ValueError("requires_due must be exactly 'true' or 'false'") from exc

    raw_disposition = _single_admin_maintenance_parameter(
        request,
        "schedule_disposition",
    )
    try:
        schedule_disposition = MaintenanceScheduleDisposition(raw_disposition)
    except ValueError as exc:
        raise ValueError("schedule_disposition is invalid") from exc
    return profile_id, requires_due, schedule_disposition


def _admin_maintenance_result_payload(
    result: MaintenanceResult,
) -> dict[str, int | str | None]:
    def serialize_datetime(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    return {
        "profile_id": result.profile_id,
        "outcome": result.outcome.value,
        "trigger": result.trigger.value,
        "sequence_before": result.sequence_before,
        "sequence_after": result.sequence_after,
        "schedule_disposition": result.schedule_disposition.value,
        "next_growth_at_before": serialize_datetime(result.next_growth_at_before),
        "next_growth_at_after": serialize_datetime(result.next_growth_at_after),
        "action_kind": result.action_kind,
        "reason": result.reason,
    }


@admin.register(BotProfile)
class BotProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "manor",
        "manor_region",
        "manor_prestige",
        "state",
        "archetype",
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "policy_version",
        "target_prestige_band",
        "current_prestige_band",
        "growth_stage",
        "loot_budget_daily",
        "last_planned_at",
        "next_growth_at",
        "is_due_for_maintenance",
        "abandon_at",
        "retire_at",
        "maintenance_started_at",
        "maintenance_stopped_at",
        "last_arena_participated_at",
        "arena_participation_count",
    )
    list_filter = (
        "state",
        "archetype",
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "policy_version",
        "target_prestige_band",
        "current_prestige_band",
        "manor__region",
        DueMaintenanceFilter,
        ("next_growth_at", admin.DateFieldListFilter),
        ("abandon_at", admin.DateFieldListFilter),
        ("retire_at", admin.DateFieldListFilter),
        ("maintenance_started_at", admin.DateFieldListFilter),
        ("maintenance_stopped_at", admin.DateFieldListFilter),
        ("last_arena_participated_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "manor__name",
        "manor__user__username",
    )
    autocomplete_fields = ("manor",)
    readonly_fields = (
        "growth_seed",
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "policy_version",
        "policy_checksum",
        "development_profile",
        "maintenance_sequence",
        "strength_budget_entries",
        "last_strength_increase_at",
        "forced_settlement_daily_budget",
        "v2_enrolled_at",
        "prestige_band",
        "target_prestige_band",
        "current_prestige_band",
        "last_planned_at",
        "maintenance_started_at",
        "maintenance_stopped_at",
        "last_arena_participated_at",
        "arena_participation_count",
        "created_at",
        "updated_at",
    )
    actions = ("mark_selected_stale",)
    date_hierarchy = "next_growth_at"
    ordering = ("next_growth_at", "id")

    def get_urls(self):
        custom_urls = [
            path(
                "maintenance-v2/",
                self.admin_site.admin_view(require_POST(self.run_v2_maintenance_view)),
                name="gameplay_botprofile_maintenance_v2",
            ),
        ]
        return custom_urls + super().get_urls()

    def run_v2_maintenance_view(self, request: HttpRequest) -> JsonResponse:
        if not self.has_change_permission(request):
            raise PermissionDenied
        try:
            profile_id, requires_due, schedule_disposition = _parse_admin_maintenance_parameters(request)
        except ValueError as exc:
            return JsonResponse(
                {
                    "error": "invalid_admin_maintenance_request",
                    "detail": str(exc),
                },
                status=400,
            )

        try:
            result = maintain_virtual_player_v2(
                profile_id,
                trigger=MaintenanceTrigger.ADMIN,
                admin_requires_due=requires_due,
                admin_schedule_disposition=schedule_disposition,
            )
        except V2MaintenanceError as exc:
            return JsonResponse(
                {
                    "error": "v2_maintenance_conflict",
                    "detail": str(exc),
                },
                status=409,
            )
        return JsonResponse(_admin_maintenance_result_payload(result))

    @admin.display(description="地区", ordering="manor__region")
    def manor_region(self, obj: BotProfile) -> str:
        return obj.manor.region

    @admin.display(description="声望", ordering="manor__prestige")
    def manor_prestige(self, obj: BotProfile) -> int:
        return int(obj.manor.prestige or 0)

    @admin.display(boolean=True, description="待维护", ordering="next_growth_at")
    def is_due_for_maintenance(self, obj: BotProfile) -> bool:
        return (
            obj.state not in {BotProfile.State.STALE, BotProfile.State.RETIRED} and obj.next_growth_at <= timezone.now()
        )

    @admin.action(description="标记为停滞")
    def mark_selected_stale(self, request, queryset) -> None:
        now = timezone.now()
        updated = mark_profiles_stale(
            tuple(queryset.values_list("id", flat=True)),
            now=now,
        )
        self.message_user(request, f"已标记 {updated} 个虚拟玩家为停滞")


@admin.register(BotInventoryDailyCounter)
class BotInventoryDailyCounterAdmin(admin.ModelAdmin):
    list_display = ("counter_date", "category", "quantity", "updated_at")
    list_filter = ("category", ("counter_date", admin.DateFieldListFilter))
    readonly_fields = (
        "category",
        "counter_date",
        "quantity",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "counter_date"
    ordering = ("-counter_date", "category")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(BotBackfillDemand)
class BotBackfillDemandAdmin(admin.ModelAdmin):
    list_display = ("region", "prestige_band", "needed", "updated_at")
    list_filter = ("region", "prestige_band")
    readonly_fields = ("region", "prestige_band", "needed", "created_at", "updated_at")
    ordering = ("region", "prestige_band")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(BotPolicyRelease)
class BotPolicyReleaseAdmin(_ReadOnlyAdmin):
    list_display = (
        "version",
        "checksum",
        "released_at",
        "retire_not_before",
        "retired_at",
    )
    list_filter = (
        ("released_at", admin.DateFieldListFilter),
        ("retired_at", admin.EmptyFieldListFilter),
    )
    search_fields = ("checksum",)
    readonly_fields = (
        "version",
        "checksum",
        "payload",
        "released_at",
        "retire_not_before",
        "retired_at",
    )
    ordering = ("-version",)


@admin.register(BotExternalStrengthReconciliation)
class BotExternalStrengthReconciliationAdmin(_ReadOnlyAdmin):
    list_display = (
        "reconciliation_identifier",
        "profile_identifier",
        "domain_event_kind",
        "domain_event_identifier",
        "status",
        "profile_attempt_count",
        "population_attempt_count",
        "available_at",
        "updated_at",
    )
    list_filter = (
        "status",
        "domain_event_kind",
        ("available_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "=profile_id",
        "domain_event_kind",
        "domain_event_id",
        "failure_code",
    )
    readonly_fields = tuple(field.name for field in BotExternalStrengthReconciliation._meta.fields)
    ordering = ("available_at", "profile_id", "origin_committed_at", "id")

    @admin.display(description="对账编号", ordering="id")
    def reconciliation_identifier(
        self,
        obj: BotExternalStrengthReconciliation,
    ) -> int:
        return int(obj.pk)

    @admin.display(description="虚拟玩家档案编号", ordering="profile_id")
    def profile_identifier(
        self,
        obj: BotExternalStrengthReconciliation,
    ) -> int:
        return int(obj.profile_id)

    @admin.display(description="领域事件编号", ordering="domain_event_id")
    def domain_event_identifier(
        self,
        obj: BotExternalStrengthReconciliation,
    ) -> str:
        return obj.domain_event_id


@admin.register(BotRuntimeRoutingState)
class BotRuntimeRoutingStateAdmin(_ReadOnlyAdmin):
    list_display = (
        "routing_key",
        "revision",
        "bootstrap_mode_label",
        "maintenance_mode_label",
        "last_hourly_safety_window_end_at",
        "last_daily_safety_window_end_at",
        "last_pause_window_identifier",
        "pause_reason",
        "updated_at",
    )
    readonly_fields = tuple(field.name for field in BotRuntimeRoutingState._meta.fields)

    @admin.display(description="路由键", ordering="key")
    def routing_key(self, obj: BotRuntimeRoutingState) -> str:
        return obj.key

    @admin.display(description="启动模式", ordering="bootstrap_mode")
    def bootstrap_mode_label(self, obj: BotRuntimeRoutingState) -> str:
        return obj.get_bootstrap_mode_display()

    @admin.display(description="维护模式", ordering="maintenance_mode")
    def maintenance_mode_label(self, obj: BotRuntimeRoutingState) -> str:
        return obj.get_maintenance_mode_display()

    @admin.display(description="最近暂停窗口编号", ordering="last_pause_window_id")
    def last_pause_window_identifier(self, obj: BotRuntimeRoutingState) -> str:
        return obj.last_pause_window_id


@admin.register(BotPopulationRecomputeDemand)
class BotPopulationRecomputeDemandAdmin(_ReadOnlyAdmin):
    list_display = (
        "region",
        "prestige_band",
        "requested_revision",
        "completed_revision",
        "claimed_revision",
        "available_at",
        "consecutive_failure_count",
        "updated_at",
    )
    list_filter = (
        "region",
        "prestige_band",
        ("available_at", admin.DateFieldListFilter),
    )
    readonly_fields = tuple(field.name for field in BotPopulationRecomputeDemand._meta.fields)
    ordering = ("available_at", "region", "prestige_band")
