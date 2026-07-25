from django.contrib import admin

from ..models import Building, BuildingType, WorkAssignment, WorkTemplate


@admin.register(BuildingType)
class BuildingTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "resource_type", "base_rate_per_hour", "rate_growth")
    list_filter = ("resource_type",)
    search_fields = ("name", "key")


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("manor", "building_type", "level", "is_upgrading", "upgrade_complete_at")
    list_filter = ("building_type", "is_upgrading")


@admin.register(WorkTemplate)
class WorkTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tier",
        "required_level",
        "required_force",
        "required_intellect",
        "required_defense",
        "required_agility",
        "reward_silver",
        "work_duration",
        "display_order",
    )
    list_filter = ("tier",)
    search_fields = ("name", "key")
    ordering = ("tier", "display_order", "required_level")


@admin.register(WorkAssignment)
class WorkAssignmentAdmin(admin.ModelAdmin):
    list_display = ("guest", "work_template", "manor", "status", "started_at", "complete_at", "reward_claimed")
    list_filter = ("status", "reward_claimed")
    search_fields = ("guest__custom_name", "guest__template__name", "manor__user__username")
    autocomplete_fields = ("manor", "guest", "work_template")
    readonly_fields = ("started_at", "complete_at", "finished_at")
    date_hierarchy = "started_at"
