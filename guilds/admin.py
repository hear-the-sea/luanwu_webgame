from django.contrib import admin

from core.admin_i18n import apply_common_field_labels, apply_model_property_labels

from .models import (
    Guild,
    GuildAnnouncement,
    GuildApplication,
    GuildBattleLineupEntry,
    GuildDonationLog,
    GuildExchangeLog,
    GuildHeroPoolEntry,
    GuildMember,
    GuildMissionRun,
    GuildMissionTemplate,
    GuildResourceLog,
    GuildTechnology,
    GuildTroopDonationLog,
    GuildTroopStorage,
    GuildWarehouse,
)

apply_common_field_labels(
    Guild,
    GuildMember,
    GuildTechnology,
    GuildWarehouse,
    GuildExchangeLog,
    GuildApplication,
    GuildAnnouncement,
    GuildDonationLog,
    GuildResourceLog,
    GuildHeroPoolEntry,
    GuildBattleLineupEntry,
    GuildMissionTemplate,
    GuildMissionRun,
    GuildTroopStorage,
    GuildTroopDonationLog,
)
apply_model_property_labels(
    Guild,
    {
        "current_member_count": "当前成员数",
        "member_capacity": "成员上限",
    },
)


@admin.register(Guild)
class GuildAdmin(admin.ModelAdmin):
    list_display = ["name", "level", "current_member_count", "member_capacity", "is_active", "created_at"]
    list_filter = ["level", "is_active", "auto_accept"]
    search_fields = ["name", "founder__username"]
    readonly_fields = ["created_at"]


@admin.register(GuildMember)
class GuildMemberAdmin(admin.ModelAdmin):
    list_display = ["user", "guild", "position", "total_contribution", "current_contribution", "is_active"]
    list_filter = ["position", "is_active", "joined_at"]
    search_fields = ["user__username", "guild__name"]
    readonly_fields = ["joined_at", "last_active_at"]


@admin.register(GuildTechnology)
class GuildTechnologyAdmin(admin.ModelAdmin):
    list_display = ["guild", "tech_key", "category", "level", "max_level", "last_production_at"]
    list_filter = ["category", "level"]
    search_fields = ["guild__name", "tech_key"]


@admin.register(GuildWarehouse)
class GuildWarehouseAdmin(admin.ModelAdmin):
    list_display = ["guild", "item_key", "quantity", "contribution_cost", "total_produced", "total_exchanged"]
    list_filter = ["guild"]
    search_fields = ["guild__name", "item_key"]


@admin.register(GuildExchangeLog)
class GuildExchangeLogAdmin(admin.ModelAdmin):
    list_display = ["guild", "member", "item_key", "quantity", "contribution_cost", "exchanged_at"]
    list_filter = ["exchanged_at"]
    search_fields = ["guild__name", "member__user__username", "item_key"]
    readonly_fields = ["exchanged_at"]


@admin.register(GuildApplication)
class GuildApplicationAdmin(admin.ModelAdmin):
    list_display = ["guild", "applicant", "status", "created_at", "reviewed_by", "reviewed_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["guild__name", "applicant__username"]
    readonly_fields = ["created_at", "reviewed_at"]


@admin.register(GuildAnnouncement)
class GuildAnnouncementAdmin(admin.ModelAdmin):
    list_display = ["guild", "type", "content", "author", "created_at"]
    list_filter = ["type", "created_at"]
    search_fields = ["guild__name", "content"]
    readonly_fields = ["created_at"]


@admin.register(GuildDonationLog)
class GuildDonationLogAdmin(admin.ModelAdmin):
    list_display = ["guild", "member", "resource_type", "amount", "contribution_gained", "donated_at"]
    list_filter = ["resource_type", "donated_at"]
    search_fields = ["guild__name", "member__user__username"]
    readonly_fields = ["donated_at"]


@admin.register(GuildResourceLog)
class GuildResourceLogAdmin(admin.ModelAdmin):
    list_display = ["guild", "action", "silver_change", "grain_change", "gold_bar_change", "related_user", "created_at"]
    list_filter = ["action", "created_at"]
    search_fields = ["guild__name", "note"]
    readonly_fields = ["created_at"]


@admin.register(GuildHeroPoolEntry)
class GuildHeroPoolEntryAdmin(admin.ModelAdmin):
    list_display = [
        "guild",
        "owner_member",
        "slot_index",
        "source_guest",
        "last_submitted_at",
    ]
    list_filter = ["guild", "slot_index", "last_submitted_at"]
    search_fields = ["guild__name", "owner_member__user__username", "source_guest__template__name"]
    readonly_fields = ["created_at", "updated_at", "last_submitted_at"]


@admin.register(GuildBattleLineupEntry)
class GuildBattleLineupEntryAdmin(admin.ModelAdmin):
    list_display = ["guild", "slot_index", "pool_entry", "selected_by", "selected_at"]
    list_filter = ["guild", "selected_at"]
    search_fields = ["guild__name", "pool_entry__owner_member__user__username"]
    readonly_fields = ["selected_at"]


@admin.register(GuildMissionTemplate)
class GuildMissionTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "key",
        "name",
        "difficulty",
        "task_type",
        "base_duration_seconds",
        "ruby_reward",
        "allow_troops",
        "is_active",
        "sort_weight",
    ]
    list_filter = ["difficulty", "task_type", "allow_troops", "is_active"]
    search_fields = ["key", "name"]


@admin.register(GuildMissionRun)
class GuildMissionRunAdmin(admin.ModelAdmin):
    list_display = [
        "guild",
        "template",
        "status",
        "started_by",
        "selected_guest_count",
        "ruby_reward",
        "started_at",
        "completed_at",
    ]
    list_filter = ["status", "started_at", "completed_at"]
    search_fields = ["guild__name", "template__key", "template__name", "started_by__user__username"]
    readonly_fields = ["started_at", "completed_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GuildTroopStorage)
class GuildTroopStorageAdmin(admin.ModelAdmin):
    list_display = ["guild", "troop_template", "count", "updated_at", "created_at"]
    list_filter = ["guild", "troop_template"]
    search_fields = ["guild__name", "troop_template__key", "troop_template__name"]
    readonly_fields = ["updated_at", "created_at"]


@admin.register(GuildTroopDonationLog)
class GuildTroopDonationLogAdmin(admin.ModelAdmin):
    list_display = ["guild", "member", "troop_template", "quantity", "donated_at"]
    list_filter = ["donated_at", "troop_template"]
    search_fields = ["guild__name", "member__user__username", "troop_template__key", "troop_template__name"]
    readonly_fields = ["donated_at"]
