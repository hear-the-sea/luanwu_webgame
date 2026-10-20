from django.contrib import admin

from ..models import ArenaWheelBout, ArenaWheelEntry, ArenaWheelEntryGuest, ArenaWheelEvent


@admin.register(ArenaWheelEvent)
class ArenaWheelEventAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "player_limit", "current_bout_number", "winner_team", "started_at", "ended_at")
    list_filter = ("status", "winner_team")
    search_fields = ("=id",)
    readonly_fields = ("base_seed", "rng_version", "battle_engine_version", "created_at", "updated_at")


@admin.register(ArenaWheelEntry)
class ArenaWheelEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "manor", "team", "team_order", "status", "debuff_stacks", "joined_at")
    list_filter = ("status", "team")
    search_fields = ("=event__id", "manor__name", "manor__user__username")
    autocomplete_fields = ("event", "manor")


@admin.register(ArenaWheelEntryGuest)
class ArenaWheelEntryGuestAdmin(admin.ModelAdmin):
    list_display = ("id", "entry", "guest", "slot_index")
    search_fields = ("=entry__id", "guest__template__name", "guest__manor__user__username")
    autocomplete_fields = ("entry", "guest")


@admin.register(ArenaWheelBout)
class ArenaWheelBoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "bout_number",
        "team_a_entry",
        "team_b_entry",
        "winner_entry",
        "battle_report",
    )
    search_fields = ("=event__id", "team_a_entry__manor__user__username", "team_b_entry__manor__user__username")
    autocomplete_fields = ("event", "team_a_entry", "team_b_entry", "winner_entry")
    raw_id_fields = ("battle_report",)
    readonly_fields = ("base_seed", "rng_version", "battle_engine_version", "created_at")
