from django.contrib import admin

from core.admin_i18n import apply_common_field_labels

from .models import (
    GearItem,
    GearTemplate,
    Guest,
    GuestDefection,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentPool,
    RecruitmentPoolEntry,
    RecruitmentRecord,
    SalaryPayment,
    TrainingLog,
    WorldUniqueGuest,
)
from .query_utils import guest_template_rarity_rank_case

apply_common_field_labels(
    GuestTemplate,
    RecruitmentPool,
    Guest,
    GearTemplate,
    GearItem,
    RecruitmentRecord,
    TrainingLog,
    RecruitmentCandidate,
    SalaryPayment,
    GuestDefection,
    WorldUniqueGuest,
)


@admin.register(GuestTemplate)
class GuestTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "key",
        "rarity",
        "archetype",
        "recruitable",
        "is_world_unique",
        "base_attack",
        "base_intellect",
        "base_defense",
    )
    list_filter = ("rarity", "archetype", "recruitable", "is_world_unique")
    search_fields = ("name", "key")
    list_editable = ("recruitable",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_rarity_rank=guest_template_rarity_rank_case("rarity"))
            .order_by("-_rarity_rank", "name")
        )

    @admin.action(description="批量设置为可招募")
    def mark_recruitable(self, request, queryset):
        queryset.update(recruitable=True)

    @admin.action(description="批量设置为不可招募")
    def mark_unrecruitable(self, request, queryset):
        queryset.update(recruitable=False)

    actions = ["mark_recruitable", "mark_unrecruitable"]


@admin.register(WorldUniqueGuest)
class WorldUniqueGuestAdmin(admin.ModelAdmin):
    list_display = ("template", "status", "owner_manor", "owner_guest", "version", "updated_at")
    list_filter = ("status",)
    search_fields = ("template__name", "template__key", "owner_manor__user__username")
    autocomplete_fields = ("template", "owner_manor", "owner_guest")


class RecruitmentPoolEntryInline(admin.TabularInline):
    model = RecruitmentPoolEntry
    extra = 1
    autocomplete_fields = ("template",)
    fields = ("template", "rarity", "archetype", "weight")


@admin.register(RecruitmentPool)
class RecruitmentPoolAdmin(admin.ModelAdmin):
    list_display = ("name", "tier", "draw_count")
    list_filter = ("tier",)
    search_fields = ("name", "key")
    ordering = ("tier", "name")
    inlines = [RecruitmentPoolEntryInline]


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = ("template", "manor", "level", "status", "created_at")
    list_filter = ("template__rarity", "status")
    search_fields = ("template__name", "manor__user__username")
    autocomplete_fields = ("template", "manor")


@admin.register(GearTemplate)
class GearTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slot", "rarity", "attack_bonus", "defense_bonus")
    list_filter = ("slot", "rarity")
    search_fields = ("name", "key")


@admin.register(GearItem)
class GearItemAdmin(admin.ModelAdmin):
    list_display = ("template", "manor", "guest", "level")
    list_filter = ("template__slot",)
    autocomplete_fields = ("template", "manor", "guest")


@admin.register(RecruitmentRecord)
class RecruitmentRecordAdmin(admin.ModelAdmin):
    list_display = ("manor", "guest", "pool", "rarity", "created_at")
    list_filter = ("rarity",)
    search_fields = ("guest__template__name", "manor__user__username", "pool__name")
    ordering = ("-created_at",)


@admin.register(TrainingLog)
class TrainingLogAdmin(admin.ModelAdmin):
    list_display = ("guest", "delta_level", "created_at")
    search_fields = ("guest__template__name",)
    ordering = ("-created_at",)


@admin.register(RecruitmentCandidate)
class RecruitmentCandidateAdmin(admin.ModelAdmin):
    list_display = ("display_name", "pool", "manor", "rarity", "created_at")
    list_filter = ("pool__tier", "rarity", "rarity_revealed")
    search_fields = ("display_name", "pool__name", "manor__user__username")
    autocomplete_fields = ("pool", "manor", "template")

    @admin.action(description="标记已显现稀有度")
    def mark_revealed(self, request, queryset):
        queryset.update(rarity_revealed=True)

    @admin.action(description="标记为未显现稀有度")
    def mark_hidden(self, request, queryset):
        queryset.update(rarity_revealed=False)

    actions = ["mark_revealed", "mark_hidden"]


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(admin.ModelAdmin):
    list_display = ("guest", "manor", "amount", "for_date", "paid_at")
    list_filter = ("for_date", "paid_at")
    search_fields = ("guest__template__name", "manor__user__username")
    autocomplete_fields = ("manor", "guest")
    readonly_fields = ("paid_at",)
    ordering = ("-paid_at",)
    date_hierarchy = "for_date"


@admin.register(GuestDefection)
class GuestDefectionAdmin(admin.ModelAdmin):
    list_display = ("guest_name", "manor", "guest_level", "guest_rarity", "loyalty_at_defection", "defected_at")
    list_filter = ("guest_rarity", "defected_at")
    search_fields = ("guest_name", "manor__user__username")
    autocomplete_fields = ("manor",)
    readonly_fields = ("defected_at",)
    ordering = ("-defected_at",)
    date_hierarchy = "defected_at"
