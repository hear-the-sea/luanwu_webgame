from django.contrib import admin

from ..models import ArenaVirtualDemand, ArenaVirtualReserveMember


class ReadOnlyCoordinatorAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ArenaVirtualDemand)
class ArenaVirtualDemandAdmin(ReadOnlyCoordinatorAdmin):
    list_display = (
        "id",
        "event_mode",
        "event_id",
        "status",
        "version",
        "guest_count_preference",
        "target_team_power",
        "missing_entry_count",
        "reserve_target_count",
        "warm_target_count",
        "max_reserve_target_count",
        "admission_attempt_high_water",
        "admission_pause_reason",
        "admission_paused_at",
        "next_retry_at",
        "last_checked_at",
        "last_progress_at",
        "last_input_change_at",
        "consecutive_failure_count",
        "last_failure_reason",
    )
    list_filter = ("status",)
    readonly_fields = (
        "tournament",
        "coop_event",
        "status",
        "version",
        "target_guest_count",
        "target_team_power",
        "missing_entry_count",
        "reserve_target_count",
        "warm_target_count",
        "max_reserve_target_count",
        "admission_attempt_high_water",
        "admission_pause_reason",
        "admission_paused_at",
        "next_retry_at",
        "last_checked_at",
        "last_progress_at",
        "last_input_change_at",
        "consecutive_failure_count",
        "last_failure_reason",
        "created_at",
        "updated_at",
    )
    ordering = ("status", "next_retry_at", "id")

    @admin.display(description="模式")
    def event_mode(self, obj: ArenaVirtualDemand) -> str:
        return "普通" if obj.tournament_id is not None else "共斗"

    @admin.display(description="门客数目标")
    def guest_count_preference(self, obj: ArenaVirtualDemand) -> int:
        return int(obj.target_guest_count)

    @admin.display(description="场次编号")
    def event_id(self, obj: ArenaVirtualDemand) -> int | None:
        return obj.tournament_id or obj.coop_event_id


@admin.register(ArenaVirtualReserveMember)
class ArenaVirtualReserveMemberAdmin(ReadOnlyCoordinatorAdmin):
    list_display = (
        "id",
        "demand",
        "profile",
        "state",
        "evaluated_version",
        "current_lineup_power",
        "roster_target_count",
        "growth_retry_streak",
        "growth_retry_reason",
        "next_acceleration_at",
        "last_checked_at",
    )
    list_filter = ("state",)
    list_select_related = ("demand", "profile", "profile__manor")
    readonly_fields = (
        "demand",
        "profile",
        "state",
        "evaluated_version",
        "current_lineup_power",
        "roster_target_count",
        "growth_retry_streak",
        "growth_retry_reason",
        "next_acceleration_at",
        "last_checked_at",
        "created_at",
        "updated_at",
    )
    ordering = ("demand_id", "state", "id")
