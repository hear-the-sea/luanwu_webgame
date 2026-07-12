from __future__ import annotations

from core.exceptions import TradeValidationError
from gameplay.models import Manor


def get_locked_listing_for_purchase(*, market_listing_model, listing_id: int):
    listing = market_listing_model.objects.select_for_update().filter(id=listing_id).first()
    if not listing:
        raise TradeValidationError("挂单不存在")
    return listing


def validate_listing_for_purchase(listing, buyer: Manor, *, active_status: str) -> None:
    if listing.status != active_status:
        raise TradeValidationError("该挂单已下架")
    if listing.is_expired:
        raise TradeValidationError("该挂单已过期")
    if listing.seller_id == buyer.pk:
        raise TradeValidationError("不能购买自己的物品")


def lock_purchase_parties(*, manor_model, buyer_pk: int, seller_pk: int | None):
    if seller_pk and buyer_pk < seller_pk:
        buyer_locked = manor_model.objects.select_for_update().get(pk=buyer_pk)
        seller_locked = manor_model.objects.select_for_update().get(pk=seller_pk)
    elif seller_pk and buyer_pk > seller_pk:
        seller_locked = manor_model.objects.select_for_update().get(pk=seller_pk)
        buyer_locked = manor_model.objects.select_for_update().get(pk=buyer_pk)
    else:
        buyer_locked = manor_model.objects.select_for_update().get(pk=buyer_pk)
        seller_locked = manor_model.objects.select_for_update().get(pk=seller_pk) if seller_pk else None
    return buyer_locked, seller_locked


def grant_listing_item_to_buyer_locked(
    *,
    buyer_locked: Manor,
    item_template,
    quantity: int,
    grant_item_locked,
) -> None:
    grant_item_locked(buyer_locked, item_key=item_template.key, quantity=quantity)
