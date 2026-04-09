from django.contrib import admin

from guests.query_utils import guest_template_rarity_rank_case

from ..models import InventoryItem, ItemTemplate


@admin.register(ItemTemplate)
class ItemTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "effect_type", "rarity", "tradeable", "price")
    list_filter = ("effect_type", "rarity", "tradeable")
    search_fields = ("name", "key")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_rarity_rank=guest_template_rarity_rank_case("rarity"))
            .order_by("effect_type", "-_rarity_rank", "name")
        )


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("manor", "template", "quantity", "updated_at")
    list_filter = ("template__effect_type", "template__rarity")
    search_fields = ("manor__user__username", "template__name")
    autocomplete_fields = ("manor", "template")
