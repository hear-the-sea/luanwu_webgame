from django.contrib import admin

from ..models import MissionRun, MissionTemplate


@admin.register(MissionTemplate)
class MissionTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "display_order",
        "battle_type",
        "daily_limit",
        "mission_card_daily_limit",
        "base_travel_time",
    )
    search_fields = ("name", "key")


@admin.register(MissionRun)
class MissionRunAdmin(admin.ModelAdmin):
    list_display = ("mission", "manor", "status", "started_at", "return_at")
    list_filter = ("status",)
