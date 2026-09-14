from django.contrib import admin

from ..models import ArenaRuntimeBudget, ArenaVirtualDemand, ArenaVirtualReserveMember
from ..models.arena_fill_service import ArenaFillServiceEpisode, ArenaFillServiceOutcome


class ReadOnlyCoordinatorAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ArenaFillServiceEpisode)
class ArenaFillServiceEpisodeAdmin(ReadOnlyCoordinatorAdmin):
    list_display = (
        "record_id",
        "mode_label",
        "event_reference",
        "virtual_fill_at_label",
        "checkpoint_at_label",
        "service_result_label",
        "first_completed_at_label",
        "terminal_reason_label",
        "publication_pending_label",
        "last_observed_at_label",
    )
    list_filter = ("mode", "slo_result", "publication_pending", "terminal_reason")
    readonly_fields = tuple(field.name for field in ArenaFillServiceEpisode._meta.fields)
    ordering = ("-first_observed_at", "-pk")

    @admin.display(ordering="pk", description="编号")
    def record_id(self, obj: ArenaFillServiceEpisode) -> int:
        return obj.pk

    @admin.display(ordering="mode", description="模式")
    def mode_label(self, obj: ArenaFillServiceEpisode) -> str:
        return obj.get_mode_display()

    @admin.display(ordering="event_id", description="场次编号")
    def event_reference(self, obj: ArenaFillServiceEpisode) -> int:
        return obj.event_id

    @admin.display(ordering="virtual_fill_at", description="补位时间")
    def virtual_fill_at_label(self, obj: ArenaFillServiceEpisode):
        return obj.virtual_fill_at

    @admin.display(ordering="checkpoint_at", description="检查点")
    def checkpoint_at_label(self, obj: ArenaFillServiceEpisode):
        return obj.checkpoint_at

    @admin.display(ordering="slo_result", description="服务结果")
    def service_result_label(self, obj: ArenaFillServiceEpisode) -> str:
        return obj.slo_result

    @admin.display(ordering="first_completed_at", description="首次完成时间")
    def first_completed_at_label(self, obj: ArenaFillServiceEpisode):
        return obj.first_completed_at

    @admin.display(ordering="terminal_reason", description="终止原因")
    def terminal_reason_label(self, obj: ArenaFillServiceEpisode) -> str:
        return obj.terminal_reason

    @admin.display(ordering="publication_pending", description="待发布")
    def publication_pending_label(self, obj: ArenaFillServiceEpisode) -> bool:
        return obj.publication_pending

    @admin.display(ordering="last_observed_at", description="最后观测时间")
    def last_observed_at_label(self, obj: ArenaFillServiceEpisode):
        return obj.last_observed_at


@admin.register(ArenaFillServiceOutcome)
class ArenaFillServiceOutcomeAdmin(ReadOnlyCoordinatorAdmin):
    list_display = (
        "record_id",
        "episode_reference",
        "phase_label",
        "result_label",
        "logical_at_label",
        "observed_at_label",
        "published_at_label",
    )
    list_filter = ("phase", "result")
    readonly_fields = tuple(field.name for field in ArenaFillServiceOutcome._meta.fields)
    ordering = ("-logical_at", "-pk")

    @admin.display(ordering="pk", description="编号")
    def record_id(self, obj: ArenaFillServiceOutcome) -> int:
        return obj.pk

    @admin.display(ordering="episode_id", description="期次编号")
    def episode_reference(self, obj: ArenaFillServiceOutcome) -> int:
        return obj.episode_id

    @admin.display(ordering="phase", description="阶段")
    def phase_label(self, obj: ArenaFillServiceOutcome) -> str:
        return obj.get_phase_display()

    @admin.display(ordering="result", description="结果")
    def result_label(self, obj: ArenaFillServiceOutcome) -> str:
        return obj.result

    @admin.display(ordering="logical_at", description="逻辑时间")
    def logical_at_label(self, obj: ArenaFillServiceOutcome):
        return obj.logical_at

    @admin.display(ordering="observed_at", description="观测时间")
    def observed_at_label(self, obj: ArenaFillServiceOutcome):
        return obj.observed_at

    @admin.display(ordering="published_at", description="发布时间")
    def published_at_label(self, obj: ArenaFillServiceOutcome):
        return obj.published_at


@admin.register(ArenaRuntimeBudget)
class ArenaRuntimeBudgetAdmin(ReadOnlyCoordinatorAdmin):
    list_display = ("budget_key", "entry_count", "updated_at_label")
    readonly_fields = tuple(field.name for field in ArenaRuntimeBudget._meta.fields)
    ordering = ("key",)

    @admin.display(description="预算条目数")
    def entry_count(self, obj: ArenaRuntimeBudget) -> int:
        return len(obj.entries) if isinstance(obj.entries, list) else -1

    @admin.display(ordering="key", description="预算类型")
    def budget_key(self, obj: ArenaRuntimeBudget) -> str:
        return {
            "materialization": "实例化",
            "training": "培养",
            "growth": "成长",
        }.get(obj.key, obj.key)

    @admin.display(ordering="updated_at", description="更新时间")
    def updated_at_label(self, obj: ArenaRuntimeBudget):
        return obj.updated_at


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
