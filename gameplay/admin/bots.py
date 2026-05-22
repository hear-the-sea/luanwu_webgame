from django.contrib import admin
from django.utils import timezone

from ..models import BotBackfillDemand, BotInventoryDailyCounter, BotProfile


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
            return queryset.exclude(state=BotProfile.State.RETIRED).filter(next_growth_at__lte=now)
        if self.value() == "no":
            return queryset.filter(next_growth_at__gt=now)
        return queryset


@admin.register(BotProfile)
class BotProfileAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "manor",
        "manor_region",
        "manor_prestige",
        "state",
        "archetype",
        "prestige_band",
        "growth_stage",
        "loot_budget_daily",
        "last_planned_at",
        "next_growth_at",
        "is_due_for_maintenance",
        "abandon_at",
        "retire_at",
        "maintenance_stopped_at",
    )
    list_filter = (
        "state",
        "archetype",
        "prestige_band",
        "manor__region",
        DueMaintenanceFilter,
        ("next_growth_at", admin.DateFieldListFilter),
        ("abandon_at", admin.DateFieldListFilter),
        ("retire_at", admin.DateFieldListFilter),
        ("maintenance_stopped_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "manor__name",
        "manor__user__username",
    )
    autocomplete_fields = ("manor",)
    readonly_fields = ("growth_seed", "last_planned_at", "maintenance_stopped_at", "created_at", "updated_at")
    actions = ("mark_selected_stale",)
    date_hierarchy = "next_growth_at"
    ordering = ("next_growth_at", "id")

    @admin.display(description="地区", ordering="manor__region")
    def manor_region(self, obj: BotProfile) -> str:
        return obj.manor.region

    @admin.display(description="声望", ordering="manor__prestige")
    def manor_prestige(self, obj: BotProfile) -> int:
        return int(obj.manor.prestige or 0)

    @admin.display(boolean=True, description="待维护", ordering="next_growth_at")
    def is_due_for_maintenance(self, obj: BotProfile) -> bool:
        return obj.state != BotProfile.State.RETIRED and obj.next_growth_at <= timezone.now()

    @admin.action(description="标记为停滞")
    def mark_selected_stale(self, request, queryset) -> None:
        now = timezone.now()
        updated = queryset.exclude(state=BotProfile.State.RETIRED).update(
            state=BotProfile.State.STALE, next_growth_at=now
        )
        self.message_user(request, f"已标记 {updated} 个虚拟玩家为停滞")


@admin.register(BotInventoryDailyCounter)
class BotInventoryDailyCounterAdmin(admin.ModelAdmin):
    list_display = ("counter_date", "category", "quantity", "updated_at")
    list_filter = ("category", ("counter_date", admin.DateFieldListFilter))
    readonly_fields = ("category", "counter_date", "quantity", "created_at", "updated_at")
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
