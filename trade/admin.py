from django.contrib import admin

from core.admin_i18n import apply_common_field_labels
from trade.models import (
    AuctionBid,
    AuctionDelivery,
    AuctionRound,
    AuctionSlot,
    FrozenGoldBar,
    GoldBarExchangeLog,
    MarketListing,
    MarketTransaction,
    ShopPurchaseLog,
    ShopSellLog,
    ShopStock,
)

apply_common_field_labels(
    ShopStock,
    ShopPurchaseLog,
    ShopSellLog,
    GoldBarExchangeLog,
    MarketListing,
    MarketTransaction,
    AuctionRound,
    AuctionSlot,
    AuctionBid,
    FrozenGoldBar,
    AuctionDelivery,
)


@admin.register(ShopStock)
class ShopStockAdmin(admin.ModelAdmin):
    list_display = ("item_key", "current_stock", "last_refresh")
    search_fields = ("item_key",)


@admin.register(ShopPurchaseLog)
class ShopPurchaseLogAdmin(admin.ModelAdmin):
    list_display = (
        "manor",
        "item_key",
        "quantity",
        "unit_price",
        "total_cost",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("manor__user__username", "item_key")
    date_hierarchy = "created_at"


@admin.register(ShopSellLog)
class ShopSellLogAdmin(admin.ModelAdmin):
    list_display = (
        "manor",
        "item_key",
        "quantity",
        "unit_price",
        "total_income",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("manor__user__username", "item_key")
    date_hierarchy = "created_at"


@admin.register(GoldBarExchangeLog)
class GoldBarExchangeLogAdmin(admin.ModelAdmin):
    list_display = (
        "manor",
        "quantity",
        "silver_cost",
        "exchange_date",
        "created_at",
    )
    list_filter = ("exchange_date",)
    search_fields = ("manor__user__username",)
    date_hierarchy = "exchange_date"


@admin.register(MarketListing)
class MarketListingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "seller",
        "item_template",
        "quantity",
        "unit_price",
        "total_price",
        "status",
        "listed_at",
        "expires_at",
    )
    list_filter = ("status", "listed_at", "duration")
    search_fields = (
        "seller__user__username",
        "item_template__name",
        "item_template__key",
    )
    date_hierarchy = "listed_at"
    readonly_fields = ("listed_at", "sold_at")
    raw_id_fields = ("seller", "buyer", "item_template")


@admin.register(MarketTransaction)
class MarketTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "get_seller",
        "buyer",
        "get_item_name",
        "total_price",
        "tax_amount",
        "seller_received",
        "transaction_at",
        "buyer_mail_sent",
        "seller_mail_sent",
    )
    list_filter = ("transaction_at", "buyer_mail_sent", "seller_mail_sent")
    search_fields = (
        "buyer__user__username",
        "listing__seller__user__username",
        "listing__item_template__name",
    )
    date_hierarchy = "transaction_at"
    readonly_fields = ("transaction_at",)
    raw_id_fields = ("listing", "buyer")

    @admin.display(description="卖家")
    def get_seller(self, obj):
        return obj.listing.seller.user.username

    @admin.display(description="物品")
    def get_item_name(self, obj):
        return f"{obj.listing.item_template.name} x{obj.listing.quantity}"


# ============ 拍卖行管理 ============


class AuctionSlotInline(admin.TabularInline):
    model = AuctionSlot
    extra = 0
    readonly_fields = (
        "item_template",
        "quantity",
        "starting_price",
        "current_price",
        "bid_count",
        "highest_bidder",
        "status",
    )
    can_delete = False


@admin.register(AuctionRound)
class AuctionRoundAdmin(admin.ModelAdmin):
    list_display = (
        "round_number",
        "status",
        "start_at",
        "end_at",
        "settled_at",
        "get_slot_count",
        "get_sold_count",
    )
    list_filter = ("status",)
    search_fields = ("round_number",)
    date_hierarchy = "start_at"
    readonly_fields = ("start_at", "settled_at")
    inlines = [AuctionSlotInline]

    @admin.display(description="拍卖位数")
    def get_slot_count(self, obj):
        return obj.slots.count()

    @admin.display(description="售出数")
    def get_sold_count(self, obj):
        return obj.slots.filter(status="sold").count()


@admin.register(AuctionSlot)
class AuctionSlotAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "round",
        "item_template",
        "quantity",
        "starting_price",
        "current_price",
        "bid_count",
        "highest_bidder",
        "status",
    )
    list_filter = ("status", "round__round_number")
    search_fields = (
        "item_template__name",
        "item_template__key",
        "highest_bidder__user__username",
    )
    raw_id_fields = ("round", "item_template", "highest_bidder")


class FrozenGoldBarInline(admin.TabularInline):
    model = FrozenGoldBar
    extra = 0
    readonly_fields = ("manor", "amount", "reason", "frozen_at", "unfrozen_at", "is_frozen")
    can_delete = False


@admin.register(AuctionBid)
class AuctionBidAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "slot",
        "manor",
        "amount",
        "status",
        "frozen_gold_bars",
        "created_at",
        "refunded_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "manor__user__username",
        "slot__item_template__name",
    )
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "refunded_at")
    raw_id_fields = ("slot", "manor")
    inlines = [FrozenGoldBarInline]


@admin.register(FrozenGoldBar)
class FrozenGoldBarAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "manor",
        "amount",
        "reason",
        "is_frozen",
        "frozen_at",
        "unfrozen_at",
    )
    list_filter = ("is_frozen", "reason", "frozen_at")
    search_fields = ("manor__user__username",)
    date_hierarchy = "frozen_at"
    readonly_fields = ("frozen_at", "unfrozen_at")
    raw_id_fields = ("manor", "auction_bid")


@admin.register(AuctionDelivery)
class AuctionDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "slot",
        "bid",
        "manor",
        "item_template",
        "quantity",
        "settlement_price",
        "status",
        "delivery_method",
        "attempts",
        "created_at",
        "delivered_at",
    )
    list_filter = ("status", "delivery_method", "created_at")
    search_fields = ("manor__user__username", "item_template__name", "item_template__key")
    readonly_fields = ("created_at", "updated_at", "delivered_at")
    raw_id_fields = ("slot", "bid", "manor", "item_template", "message")
