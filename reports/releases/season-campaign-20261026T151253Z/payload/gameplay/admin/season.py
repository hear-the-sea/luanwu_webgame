"""赛季后台只读审计；开季与结算使用领域命令。"""

from django.contrib import admin

from core.admin_records import ReadOnlyAdmin
from gameplay.models.season import (
    Season,
    SeasonAction,
    SeasonClaim,
    SeasonExpedition,
    SeasonFaction,
    SeasonIntel,
    SeasonMember,
    SeasonRally,
    SeasonSite,
    SeasonWeek,
)


@admin.register(Season)
class SeasonAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "name",
        "starts_at",
        "ends_at",
        "closed",
        "settled_weeks",
        "fiction_score",
        "history_score",
        "winner",
    )
    list_filter = ("closed", "winner")
    search_fields = ("name",)


@admin.register(SeasonMember)
class SeasonMemberAdmin(ReadOnlyAdmin):
    list_display = ("id", "season", "manor", "faction", "merit", "coins", "scroll_available", "scroll_used")
    list_filter = ("faction", "season")
    list_select_related = ("season", "manor")
    search_fields = ("manor__name", "manor__user__username")


@admin.register(SeasonAction)
class SeasonActionAdmin(ReadOnlyAdmin):
    list_display = ("id", "member", "kind", "node_key", "week", "war_points", "created_at")
    list_filter = ("kind", "week")
    list_select_related = ("member",)
    search_fields = ("node_key", "member__manor__user__username")


@admin.register(SeasonClaim)
class SeasonClaimAdmin(ReadOnlyAdmin):
    list_display = ("id", "member", "key", "quantity", "created_at")
    list_select_related = ("member",)


@admin.register(SeasonWeek)
class SeasonWeekAdmin(ReadOnlyAdmin):
    list_display = (
        "season",
        "number",
        "node_key",
        "fiction_points",
        "history_points",
        "matched_members",
        "winner",
        "settled_at",
    )
    list_select_related = ("season",)
    list_filter = ("season", "winner")


@admin.register(SeasonFaction)
class SeasonFactionAdmin(ReadOnlyAdmin):
    list_display = ("id", "season", "faction", "score", "produced_at")
    list_filter = ("season", "faction")
    list_select_related = ("season",)


@admin.register(SeasonSite)
class SeasonSiteAdmin(ReadOnlyAdmin):
    list_display = ("id", "season", "key", "owner", "guards", "durability", "control", "protected_until")
    list_filter = ("season", "owner")
    list_select_related = ("season",)


@admin.register(SeasonExpedition)
class SeasonExpeditionAdmin(ReadOnlyAdmin):
    list_display = ("id", "member", "node_key", "kind", "status", "due_at", "battles", "error")
    list_filter = ("status", "kind")
    list_select_related = ("member",)


@admin.register(SeasonRally)
class SeasonRallyAdmin(ReadOnlyAdmin):
    list_display = ("id", "season", "leader", "node_key", "kind", "status", "gathers_until", "due_at")
    list_filter = ("season", "status", "kind")
    list_select_related = ("season", "leader__manor")


@admin.register(SeasonIntel)
class SeasonIntelAdmin(ReadOnlyAdmin):
    list_display = ("id", "season", "member", "faction", "node_key", "kind", "expires_at")
    list_filter = ("season", "faction", "kind")
    list_select_related = ("season", "member__manor")
