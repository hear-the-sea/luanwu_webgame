from django.contrib import admin
from django.utils import timezone

from core.admin_i18n import apply_common_field_labels
from core.admin_records import ReadOnlyAdmin

from .admin_forms import SkillAdminForm
from .models import (
    GearItem,
    GearTemplate,
    Guest,
    GuestDefection,
    GuestRecruitment,
    GuestSkill,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentExtraAttempt,
    RecruitmentPool,
    RecruitmentPoolEntry,
    RecruitmentRecord,
    SalaryPayment,
    Skill,
    SkillBook,
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
    RecruitmentPoolEntry,
    RecruitmentExtraAttempt,
    GuestRecruitment,
    GuestSkill,
    Skill,
    SkillBook,
    labels={
        "weight": "抽取权重",
        "skill": "技能",
        "source": "来源",
        "learned_at": "学习时间",
        "base_power": "基础威力",
        "base_probability": "旧版触发概率",
        "status_effect": "状态效果",
        "status_probability": "状态施加概率",
        "status_duration": "状态持续回合",
        "damage_formula": "伤害公式",
        "passive_config": "被动技能配置",
        "required_force": "武力要求",
        "required_intellect": "智力要求",
        "required_defense": "防御要求",
        "required_agility": "敏捷要求",
        "targets": "目标数量",
        "bot_profile": "虚拟玩家档案",
        "operation_id": "招募操作编号",
        "base_key": "基础装备标识",
        "base_name": "基础装备名称",
    },
)


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    form = SkillAdminForm
    list_display = ("name", "key", "kind", "rarity", "base_power", "required_level", "targets")
    list_filter = ("kind", "rarity")
    search_fields = ("name", "key", "description")
    # 旧版触发概率已不参与战斗计算，避免误导运营配置。
    exclude = ("base_probability",)


@admin.register(SkillBook)
class SkillBookAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "skill")
    search_fields = ("name", "key", "skill__name", "skill__key")
    autocomplete_fields = ("skill",)
    list_select_related = ("skill",)


class GuestSkillInline(admin.TabularInline):
    model = GuestSkill
    fields = ("skill", "source", "learned_at")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("skill")


class RecruitmentDueFilter(admin.SimpleListFilter):
    title = "招募进度"
    parameter_name = "recruitment_due"

    def lookups(self, request, model_admin):
        return (("overdue", "已到期未完成"), ("waiting", "招募倒计时中"))

    def queryset(self, request, queryset):
        if self.value() == "overdue":
            return queryset.filter(status=GuestRecruitment.Status.PENDING, complete_at__lte=timezone.now())
        if self.value() == "waiting":
            return queryset.filter(status=GuestRecruitment.Status.PENDING, complete_at__gt=timezone.now())
        return queryset


@admin.register(GuestRecruitment)
class GuestRecruitmentAdmin(ReadOnlyAdmin):
    list_display = (
        "id",
        "manor",
        "pool",
        "source",
        "status",
        "draw_count",
        "result_count",
        "complete_at",
        "error_message",
    )
    list_filter = ("status", "source", RecruitmentDueFilter, "started_at")
    search_fields = ("manor__user__username", "manor__name", "pool__name", "operation_id", "error_message")
    list_select_related = ("manor__user", "pool")
    date_hierarchy = "started_at"
    ordering = ("-started_at",)


@admin.register(RecruitmentExtraAttempt)
class RecruitmentExtraAttemptAdmin(ReadOnlyAdmin):
    list_display = ("manor", "pool", "date", "extra_count", "updated_at")
    list_filter = ("date", "pool")
    search_fields = ("manor__user__username", "manor__name", "pool__name")
    list_select_related = ("manor__user", "pool")
    date_hierarchy = "date"
    ordering = ("-date",)


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

    @admin.action(permissions=["change"], description="批量设置为可招募")
    def mark_recruitable(self, request, queryset):
        queryset.update(recruitable=True)

    @admin.action(permissions=["change"], description="批量设置为不可招募")
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
    inlines = (GuestSkillInline,)


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

    @admin.action(permissions=["change"], description="标记已显现稀有度")
    def mark_revealed(self, request, queryset):
        queryset.update(rarity_revealed=True)

    @admin.action(permissions=["change"], description="标记为未显现稀有度")
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
