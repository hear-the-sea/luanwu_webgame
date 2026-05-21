from django.contrib import admin

from common.constants.resources import ResourceType

from ..models import Manor, ResourceEvent


@admin.register(Manor)
class ManorAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "name",
        "region",
        "coordinate_x",
        "coordinate_y",
        "prestige",
        "action_points",
        "grain",
        "silver",
        "arena_coins",
    )
    list_filter = ("region",)
    search_fields = ("user__username", "user__email", "name")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "resource_updated_at", "last_active_at")
    fieldsets = (
        (
            "基本信息",
            {
                "fields": (
                    "user",
                    "name",
                    "prestige",
                    "prestige_silver_spent",
                    "action_points",
                    "action_points_updated_at",
                )
            },
        ),
        ("位置信息", {"fields": ("region", "coordinate_x", "coordinate_y", "last_active_at")}),
        (
            "资源",
            {
                "fields": (
                    "grain",
                    "silver",
                    "arena_coins",
                    "grain_capacity",
                    "silver_capacity",
                    "storage_capacity",
                    "retainer_count",
                )
            },
        ),
        (
            "保护状态",
            {
                "fields": (
                    "newbie_protection_until",
                    "defeat_protection_until",
                    "peace_shield_until",
                    "last_relocation_at",
                ),
                "classes": ("collapse",),
            },
        ),
        ("时间", {"fields": ("created_at", "resource_updated_at"), "classes": ("collapse",)}),
    )


@admin.register(ResourceEvent)
class ResourceEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "manor_link",
        "resource_type_display",
        "delta_display",
        "reason_display",
        "note",
        "created_at",
    )
    list_filter = (
        "resource_type",
        "reason",
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = ("manor__user__username", "manor__name", "note")
    readonly_fields = ("manor", "resource_type", "delta", "reason", "note", "created_at")
    date_hierarchy = "created_at"
    list_per_page = 50
    ordering = ("-created_at",)

    # 禁止添加和修改（流水记录只读）
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="庄园", ordering="manor__user__username")
    def manor_link(self, obj):
        """显示庄园链接"""
        from django.urls import reverse
        from django.utils.html import format_html

        url = reverse("admin:gameplay_manor_change", args=[obj.manor_id])
        return format_html('<a href="{}">{}</a>', url, obj.manor.name or obj.manor.user.username)

    @admin.display(description="资源类型", ordering="resource_type")
    def resource_type_display(self, obj):
        """资源类型中文显示"""
        labels = dict(ResourceType.choices)
        icons = {"grain": "🌾", "silver": "💰"}
        label = labels.get(obj.resource_type, obj.resource_type)
        icon = icons.get(obj.resource_type, "")
        return f"{icon} {label}".strip()

    @admin.display(description="变化量", ordering="delta")
    def delta_display(self, obj):
        """数量显示（带颜色）"""
        from django.utils.html import format_html

        if obj.delta > 0:
            return format_html('<span style="color: green;">+{}</span>', obj.delta)
        elif obj.delta < 0:
            return format_html('<span style="color: red;">{}</span>', obj.delta)
        return obj.delta

    @admin.display(description="原因", ordering="reason")
    def reason_display(self, obj):
        """原因中文显示"""
        reason_map = {
            "produce": "🌾 自动产出",
            "upgrade_cost": "🏗️ 建筑升级",
            "task_reward": "🎁 任务奖励",
            "battle_reward": "⚔️ 战斗掉落",
            "admin_adjust": "⚙️ 运营调整",
            "recruit_cost": "👥 门客招募",
            "training_cost": "📖 门客培养",
            "item_use": "🎒 道具使用",
            "shop_purchase": "🛒 商铺购买",
            "shop_sell": "💵 商铺出售",
            "work_reward": "💼 打工报酬",
            "guild_donation": "🏛️ 帮会捐献",
            "market_listing_fee": "📋 交易行挂单手续费",
            "market_purchase": "🛍️ 交易行购买",
            "market_sold": "💰 交易行出售",
            "item_sold": "📦 物品出售",
            "tech_upgrade": "📚 科技升级",
        }
        return reason_map.get(obj.reason, obj.reason)
