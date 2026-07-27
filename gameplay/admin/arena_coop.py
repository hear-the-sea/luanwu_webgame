from django.contrib import admin

from ..models import ArenaCoopEvent


@admin.register(ArenaCoopEvent)
class ArenaCoopEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "player_limit",
        "boss_name",
        "boss_remaining_hp",
        "base_seed",
        "rng_version",
        "battle_engine_version",
        "prepare_ends_at",
        "ended_at",
    )
    list_filter = ("status", "boss_defeated")
    search_fields = ("=id", "boss_name", "=battle_report__id")
    readonly_fields = (
        "base_seed",
        "rng_version",
        "battle_engine_version",
        "battle_report",
        "enemy_snapshot",
        "reward_snapshot",
        "daily_rule_snapshot",
        "created_at",
        "updated_at",
    )
