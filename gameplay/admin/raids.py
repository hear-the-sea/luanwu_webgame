from django.contrib import admin

from ..models import RaidRun, ScoutCooldown, ScoutRecord

# ============ PVP 踢馆系统 Admin ============


@admin.register(ScoutRecord)
class ScoutRecordAdmin(admin.ModelAdmin):
    list_display = ("attacker", "defender", "status", "success_rate", "started_at", "complete_at")
    list_filter = ("status",)
    search_fields = ("attacker__user__username", "defender__user__username")
    readonly_fields = ("started_at", "completed_at", "intel_data")
    date_hierarchy = "started_at"


@admin.register(ScoutCooldown)
class ScoutCooldownAdmin(admin.ModelAdmin):
    list_display = ("attacker", "defender", "cooldown_until", "is_active")
    search_fields = ("attacker__user__username", "defender__user__username")

    @admin.display(boolean=True, description="冷却中")
    def is_active(self, obj):
        return obj.is_active


@admin.register(RaidRun)
class RaidRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "attacker",
        "defender",
        "status",
        "failure_reason",
        "base_seed",
        "rng_version",
        "battle_engine_version",
        "is_attacker_victory",
        "started_at",
        "battle_at",
        "return_at",
    )
    list_filter = ("status", "is_attacker_victory")
    search_fields = ("attacker__user__username", "defender__user__username")
    readonly_fields = (
        "started_at",
        "battle_at",
        "return_at",
        "completed_at",
        "failure_reason",
        "resources_released",
        "base_seed",
        "rng_version",
        "battle_engine_version",
        "loot_resources",
        "loot_items",
        "attacker_prestige_change",
        "defender_prestige_change",
    )
    date_hierarchy = "started_at"
    filter_horizontal = ("guests",)
