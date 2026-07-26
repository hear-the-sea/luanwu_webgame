from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.exceptions import TradeValidationError


def create_market_listing(
    manor,
    item_key: str,
    quantity: int,
    unit_price: int,
    duration: int,
    *,
    normalize_listing_inputs,
    listing_fees,
    load_market_item_template,
    validate_listing_price,
    validate_market_buy_or_list_access,
    manor_model,
    get_listing_fee,
    charge_listing_fee,
    lock_market_listing_inventory_item,
    get_frozen_gold_bars,
    validate_gold_bar_availability,
    validate_listing_inventory,
    decrement_market_listing_inventory,
    validate_listing_total_price,
    create_listing_record,
    market_listing_model,
    max_total_price: int,
    safe_int,
):
    quantity, unit_price, duration = normalize_listing_inputs(quantity, unit_price, duration, safe_int=safe_int)

    if duration not in listing_fees:
        raise TradeValidationError(f"无效的上架时长，请选择 {list(listing_fees.keys())}")

    item_template = load_market_item_template(item_key)
    validate_listing_price(item_template, unit_price)

    if quantity <= 0:
        raise TradeValidationError("数量必须大于0")

    with transaction.atomic():
        locked_manor = manor_model.objects.select_for_update().get(pk=manor.pk)
        validate_market_buy_or_list_access(locked_manor)
        listing_fee = get_listing_fee(duration)
        charge_listing_fee(locked_manor, listing_fee)

        inventory_item = lock_market_listing_inventory_item(
            locked_manor=locked_manor,
            item_template=item_template,
        )
        validate_listing_inventory(inventory_item=inventory_item, quantity=quantity)

        if item_template.key == "gold_bar":
            frozen = get_frozen_gold_bars(manor)
            validate_gold_bar_availability(inventory_item=inventory_item, quantity=quantity, frozen=frozen)

        decrement_market_listing_inventory(inventory_item=inventory_item, quantity=quantity)

        total_price = validate_listing_total_price(
            unit_price=unit_price,
            quantity=quantity,
            max_total_price=max_total_price,
        )
        return create_listing_record(
            market_listing_model=market_listing_model,
            locked_manor=locked_manor,
            item_template=item_template,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
            duration=duration,
            listing_fee=listing_fee,
        )


def purchase_market_listing(
    buyer,
    listing_id: int,
    *,
    market_listing_model,
    market_transaction_model,
    manor_model,
    get_locked_listing_for_purchase,
    validate_listing_for_purchase,
    lock_purchase_parties,
    validate_market_buy_or_list_access,
    pay_market_purchase,
    settle_market_sale_proceeds,
    grant_listing_item_to_buyer_locked,
    grant_market_item_locked,
    transaction_tax_rate: float,
    send_purchase_notifications,
):
    with transaction.atomic():
        listing = get_locked_listing_for_purchase(
            market_listing_model=market_listing_model,
            listing_id=listing_id,
        )
        validate_listing_for_purchase(
            listing,
            buyer,
            active_status=market_listing_model.Status.ACTIVE,
        )
        buyer_locked, seller_locked = lock_purchase_parties(
            manor_model=manor_model,
            buyer_pk=buyer.pk,
            seller_pk=listing.seller_id,
        )
        validate_market_buy_or_list_access(buyer_locked)

        pay_market_purchase(
            buyer_locked,
            item_name=listing.item_template.name,
            total_price=listing.total_price,
        )

        tax_amount = int(listing.total_price * transaction_tax_rate)
        seller_received = listing.total_price - tax_amount

        if seller_locked is not None:
            settle_market_sale_proceeds(
                seller_locked,
                item_name=listing.item_template.name,
                silver_amount=seller_received,
            )

        now = timezone.now()
        listing.status = market_listing_model.Status.SOLD
        listing.buyer = buyer_locked
        listing.sold_at = now
        listing.save(update_fields=["status", "buyer", "sold_at"])

        transaction_record = market_transaction_model.objects.create(
            listing=listing,
            buyer=buyer_locked,
            total_price=listing.total_price,
            tax_amount=tax_amount,
            seller_received=seller_received,
        )

        grant_listing_item_to_buyer_locked(
            buyer_locked=buyer_locked,
            item_template=listing.item_template,
            quantity=listing.quantity,
            grant_item_locked=grant_market_item_locked,
        )

    buyer_mail_sent, seller_mail_sent = send_purchase_notifications(
        buyer=buyer,
        listing=listing,
        tax_amount=tax_amount,
        seller_received=seller_received,
    )
    transaction_record.buyer_mail_sent = buyer_mail_sent
    transaction_record.seller_mail_sent = seller_mail_sent
    transaction_record.save(update_fields=["buyer_mail_sent", "seller_mail_sent"])
    return transaction_record


def cancel_market_listing(
    manor,
    listing_id: int,
    *,
    manor_model,
    market_listing_model,
    restore_cancelled_listing_inventory,
    build_cancel_listing_result,
    grant_market_item_locked,
):
    with transaction.atomic():
        listing = market_listing_model.objects.select_for_update().filter(id=listing_id, seller=manor).first()

        if not listing:
            raise TradeValidationError("挂单不存在或无权取消")

        if listing.status != market_listing_model.Status.ACTIVE:
            raise TradeValidationError("该挂单已经不在售状态，无法取消")

        locked_manor = manor_model.objects.select_for_update().get(pk=listing.seller_id)

        listing.status = market_listing_model.Status.CANCELLED
        listing.save(update_fields=["status"])

        restore_cancelled_listing_inventory(
            manor=locked_manor,
            listing=listing,
            grant_item_locked=grant_market_item_locked,
        )
        return build_cancel_listing_result(listing=listing)
