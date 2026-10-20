"""专属身份与一次性卡片不能从普通处置入口流失。"""

from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from accounts.custom_guest_grant import grant_custom_guest_card_on_entry
from core.exceptions import GameError, GuestError, ItemNotConfiguredError, JailError, TradeValidationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.jail import add_oath_bond
from gameplay.services.raid.combat.capture import _filter_capture_candidates, _select_capture_target
from guests.models import CustomGuestProgress, Guest, GuestStatus, GuestTemplate
from guests.services.custom_guest import ensure_custom_guest_card
from guests.services.recruitment_guests import create_guest_from_template
from guests.services.roster import dismiss_guest
from guests.tasks import _process_defection_batch
from trade.services.market_listing_helpers import load_tradeable_item_template
from trade.services.shop_config import get_sell_price_by_template
from trade.services.shop_service import sell_item

pytestmark = pytest.mark.django_db


@pytest.fixture
def custom_guest(manor_factory):
    manor, _ = manor_factory()
    template, _ = GuestTemplate.objects.get_or_create(
        key="custom_guest_green",
        defaults={"name": "专属门客", "rarity": "green", "archetype": "warrior", "recruitable": False},
    )
    return Guest.objects.create(manor=manor, template=template, loyalty=0)


@pytest.mark.parametrize("status", [GuestStatus.IDLE, GuestStatus.VACATION, GuestStatus.INJURED])
def test_custom_guest_cannot_be_dismissed(custom_guest, status):
    custom_guest.status = status
    custom_guest.save(update_fields=["status"])
    with pytest.raises(GuestError, match="不可辞退"):
        dismiss_guest(custom_guest)
    assert Guest.objects.filter(pk=custom_guest.pk).exists()


def test_custom_guest_cannot_be_oathed(custom_guest):
    with pytest.raises(JailError, match="不可结义"):
        add_oath_bond(custom_guest.manor, custom_guest.pk)


def test_custom_template_cannot_be_granted_through_generic_recruitment(custom_guest):
    with pytest.raises(GameError, match="只能通过自定义门客卡"):
        create_guest_from_template(manor=custom_guest.manor, template=custom_guest.template)


def test_custom_guest_is_excluded_from_capture_and_rechecked_under_lock(custom_guest):
    normal_template = GuestTemplate.objects.create(
        key="protection_normal", name="普通", rarity="green", archetype="warrior"
    )
    normal = Guest.objects.create(manor=custom_guest.manor, template=normal_template)
    assert _filter_capture_candidates([custom_guest.pk, normal.pk]) == [normal.pk]
    assert _select_capture_target([custom_guest.pk], custom_guest.manor, rng=random.Random(1)) is None


def test_custom_guest_cannot_defect_even_when_directly_scheduled(custom_guest):
    send_message = Mock()
    assert _process_defection_batch([custom_guest.pk], create_message=send_message) == 0
    assert Guest.objects.filter(pk=custom_guest.pk, loyalty=0).exists()
    send_message.assert_not_called()


def test_custom_card_cannot_be_sold_or_listed_even_if_configuration_or_binding_is_wrong(manor_factory):
    manor, _ = manor_factory()
    template, _ = ItemTemplate.objects.update_or_create(
        key="custom_guest_card",
        defaults={"name": "自定义门客卡", "price": 100, "tradeable": True, "effect_type": "tool"},
    )
    card = InventoryItem.objects.get(manor=manor, template=template)
    card.bound_quantity = 0
    card.save(update_fields=["bound_quantity"])
    with pytest.raises(ItemNotConfiguredError, match="不可出售"):
        sell_item(manor, template.key, 1)
    with pytest.raises(TradeValidationError, match="不可交易"):
        load_tradeable_item_template(item_template_model=ItemTemplate, item_key=template.key)
    assert get_sell_price_by_template(template) == 0
    assert InventoryItem.objects.get(manor=manor, template=template).quantity == 1


def test_entry_grants_exactly_one_bound_card_and_never_replenishes_consumed_card(manor_factory):
    manor, user = manor_factory()
    ItemTemplate.objects.update_or_create(
        key="custom_guest_card", defaults={"name": "自定义门客卡", "effect_type": "tool"}
    )
    grant_custom_guest_card_on_entry(user, manor=manor)
    grant_custom_guest_card_on_entry(user, manor=manor)
    card = InventoryItem.objects.get(manor=manor, template__key="custom_guest_card")
    assert (card.quantity, card.bound_quantity) == (1, 1)
    card.delete()
    grant_custom_guest_card_on_entry(user, manor=manor)
    assert not InventoryItem.objects.filter(manor=manor, template__key="custom_guest_card").exists()
    assert CustomGuestProgress.objects.get(manor=manor).card_issued


def test_missing_card_template_does_not_mark_account_issued(manor_factory):
    ItemTemplate.objects.filter(key="custom_guest_card").delete()
    manor, user = manor_factory()
    grant_custom_guest_card_on_entry(user, manor=manor)
    assert not CustomGuestProgress.objects.filter(manor=manor, card_issued=True).exists()


def test_failed_card_grant_rolls_back_issue_marker_and_can_retry(manor_factory):
    manor, _ = manor_factory()
    # 构造功能上线前尚未领卡的历史庄园。
    InventoryItem.objects.filter(manor=manor, template__key="custom_guest_card").delete()
    CustomGuestProgress.objects.filter(manor=manor).delete()
    ItemTemplate.objects.update_or_create(
        key="custom_guest_card", defaults={"name": "自定义门客卡", "effect_type": "tool"}
    )
    with patch("guests.services.custom_guest.add_item_to_inventory_locked", side_effect=GuestError("仓库已满")):
        with pytest.raises(GuestError):
            ensure_custom_guest_card(manor)
    assert not CustomGuestProgress.objects.filter(manor=manor, card_issued=True).exists()
    assert ensure_custom_guest_card(manor)


def test_login_signal_calls_entry_grant(manor_factory):
    _, user = manor_factory()
    with patch("accounts.custom_guest_grant.grant_custom_guest_card_on_entry") as grant:
        from accounts.signals import grant_custom_guest_card_on_login

        grant_custom_guest_card_on_login(sender=type(user), request=SimpleNamespace(), user=user)
    grant.assert_called_once_with(user)
