"""Equipment rolls belong to creation, while delivery preserves their identity."""

from __future__ import annotations

import logging

import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl.drops import award_mission_drops
from guests.models import GearSlot, GearTemplate

pytestmark = pytest.mark.django_db


def _equipment():
    template = ItemTemplate.objects.create(
        key="reward_affix_blade",
        name="奖励长剑",
        rarity="purple",
        effect_type="equip_weapon",
        effect_payload={"force": 40, "agility": 20, "defense": 10, "intellect": 10},
    )
    GearTemplate.objects.create(
        key=template.key,
        name=template.name,
        rarity="purple",
        slot=GearSlot.WEAPON,
        extra_stats={"force": 40, "agility": 20, "defense": 10, "intellect": 10},
    )
    return template


class _AlwaysPrefix:
    def random(self):
        return 0.0

    def choice(self, values):
        return values[0]


def _force_roll(monkeypatch, target):
    from gameplay.services.equipment_affixes import roll_equipment_reward_items_locked

    def roll(items, *, templates=None, rng=None):
        return roll_equipment_reward_items_locked(items, templates=templates, rng=_AlwaysPrefix())

    monkeypatch.setattr(target, roll)


def test_mission_reward_equipment_rolls_only_once(django_user_model, monkeypatch):
    from gameplay.services import equipment_affixes
    from gameplay.services.inventory.core import add_item_to_inventory

    manor = ensure_manor(django_user_model.objects.create_user(username="affix_mission"))
    base = _equipment()
    _force_roll(monkeypatch, "gameplay.services.missions_impl.drops.roll_equipment_reward_items_locked")
    award_mission_drops(manor, {base.key: 2}, note="前缀掉落")
    item = InventoryItem.objects.get(manor=manor)
    assert item.template.key != base.key
    assert item.quantity == 2
    assert item.template.name != base.name

    # Ordinary inventory delivery is deliberately outside the generation API.
    monkeypatch.setattr(
        equipment_affixes,
        "roll_equipment_reward_items_locked",
        lambda *args, **kwargs: pytest.fail("delivery must not reroll"),
    )
    add_item_to_inventory(manor, item.template.key, 1)
    item.refresh_from_db()
    assert item.quantity == 3


def test_loot_box_displays_actual_variant_and_preserves_binding(django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="affix_loot_box"))
    base = _equipment()
    _force_roll(monkeypatch, "gameplay.services.inventory.use.roll_equipment_reward_items_locked")
    chest_template = ItemTemplate.objects.create(
        key="affix_reward_box",
        name="装备宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={"item_rewards": [{"item_key": base.key, "min_quantity": 2}]},
    )
    chest = InventoryItem.objects.create(manor=manor, template=chest_template, quantity=1, bound_quantity=1)
    result = use_inventory_item(chest)
    item = InventoryItem.objects.get(manor=manor)
    assert item.template.key != base.key
    assert (item.quantity, item.bound_quantity) == (2, 2)
    assert item.template.name in result["_message"]


def test_auction_freezes_variant_before_bidding_and_keeps_it_on_delivery(django_user_model, monkeypatch):
    from trade.services.auction.rounds_delivery_support import grant_auction_item_directly_impl
    from trade.services.auction.rounds_lifecycle_support import create_auction_round_impl
    from trade.services.auction_config import AuctionItemConfig, AuctionSettings

    manor = ensure_manor(django_user_model.objects.create_user(username="affix_auction"))
    base = _equipment()
    _force_roll(monkeypatch, "trade.services.auction.rounds_lifecycle_support.roll_equipment_reward_items_locked")
    monkeypatch.setattr(
        "trade.services.auction.rounds_lifecycle_support.release_cache_key_if_owner", lambda *args, **kwargs: None
    )
    auction_round = create_auction_round_impl(
        safe_cache_add_func=lambda *args: True,
        logger=logging.getLogger(__name__),
        get_settings_func=lambda: AuctionSettings(cycle_days=3, min_increment_ratio=0.1, default_min_increment=1),
        get_enabled_items_func=lambda: [
            AuctionItemConfig(
                item_key=base.key, slots=1, quantity_per_slot=3, starting_price=10, min_increment=1, enabled=True
            )
        ],
    )
    slot = auction_round.slots.select_related("item_template").get()
    assert slot.config_key == base.key
    assert slot.item_template.key != base.key
    monkeypatch.setattr(
        "gameplay.services.equipment_affixes.roll_equipment_reward_items_locked",
        lambda *args, **kwargs: pytest.fail("auction delivery must not reroll"),
    )
    grant_auction_item_directly_impl(manor, slot.item_template, 1, safe_int_func=lambda value, fallback: int(value))
    assert InventoryItem.objects.get(manor=manor).template_id == slot.item_template_id


def test_blueprint_variants_keep_individual_binding_and_report_quantities(django_user_model, monkeypatch):
    from gameplay.services.buildings import forge

    manor = ensure_manor(django_user_model.objects.create_user(username="affix_blueprint"))
    base = _equipment()
    blueprint = ItemTemplate.objects.create(key="blueprint_affix_test", name="长剑图纸", effect_type="tool")
    InventoryItem.objects.create(manor=manor, template=blueprint, quantity=2, bound_quantity=1)
    _force_roll(monkeypatch, "gameplay.services.buildings.forge_blueprints.roll_equipment_reward_items_locked")
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 5)
    monkeypatch.setattr(
        forge,
        "_build_blueprint_recipe_index",
        lambda: {
            blueprint.key: {
                "blueprint_key": blueprint.key,
                "result_item_key": base.key,
                "required_forging": 1,
                "quantity_out": 2,
                "costs": {},
            }
        },
    )
    result = forge.synthesize_equipment_with_blueprint(manor, blueprint.key, quantity=2)
    item = InventoryItem.objects.get(manor=manor)
    assert item.template.key != base.key
    assert (item.quantity, item.bound_quantity) == (4, 2)
    assert result["items"] == {item.template.key: 4}


def test_legacy_gear_only_mission_reward_keeps_stats_and_gains_prefix(django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="affix_legacy_mission"))
    gear_template = GearTemplate.objects.create(
        key="legacy_affix_mission",
        name="旧制长剑",
        rarity="purple",
        slot=GearSlot.WEAPON,
        extra_stats={"force": 40, "agility": 20},
        attack_bonus=7,
        defense_bonus=3,
        required_level=45,
    )
    _force_roll(monkeypatch, "gameplay.services.missions_impl.drops.roll_equipment_reward_items_locked")
    award_mission_drops(manor, {gear_template.key: 1}, note="旧制装备掉落")
    item = InventoryItem.objects.get(manor=manor)
    assert item.template.key != gear_template.key
    assert item.template.effect_payload["required_level"] == 45
    assert item.template.effect_payload["equipment_attack_bonus"] == 7
    assert item.template.effect_payload["equipment_defense_bonus"] == 3
    assert item.template.effect_payload["force"] + item.template.effect_payload["agility"] == 60


def test_legacy_gear_only_chest_keeps_bonuses_and_displays_prefix(django_user_model, monkeypatch):
    from guests.models import GearItem

    manor = ensure_manor(django_user_model.objects.create_user(username="affix_legacy_box"))
    base = GearTemplate.objects.create(
        key="legacy_affix_box",
        name="古旧宝剑",
        rarity="purple",
        slot=GearSlot.WEAPON,
        extra_stats={"force": 40, "agility": 20},
        attack_bonus=7,
        defense_bonus=3,
        required_level=45,
    )
    _force_roll(monkeypatch, "gameplay.services.equipment_affixes.roll_equipment_reward_items_locked")
    chest_template = ItemTemplate.objects.create(
        key="affix_legacy_chest",
        name="旧制装备箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={"gear_keys": [base.key], "gear_chance": 1},
    )
    chest = InventoryItem.objects.create(manor=manor, template=chest_template, quantity=1)
    result = use_inventory_item(chest)
    gear = GearItem.objects.select_related("template").get(manor=manor)
    assert gear.template.key != base.key
    assert gear.template.name in result["_message"]
    assert (gear.template.attack_bonus, gear.template.defense_bonus, gear.template.required_level) == (7, 3, 45)


def test_campaign_fixes_each_recipient_variant_once_and_claim_keeps_binding(django_user_model, monkeypatch):
    from gameplay.models import GlobalMailCampaign, Message
    from gameplay.services import equipment_reward_attachments
    from gameplay.services.equipment_affixes import roll_equipment_reward_items_locked
    from gameplay.services.global_mail import deliver_campaign_to_manor
    from gameplay.services.utils.messages import claim_message_attachments

    manor = ensure_manor(django_user_model.objects.create_user(username="affix_campaign_a"))
    other = ensure_manor(django_user_model.objects.create_user(username="affix_campaign_b"))
    base = _equipment()
    original = {"items": {base.key: 2}, "bound_items": {base.key: 1}, "resources": {"silver": 10}}
    campaign = GlobalMailCampaign.objects.create(key="affix_campaign", title="装备活动", attachments=original)
    calls = []

    def roll(items, *, templates=None):
        calls.append(dict(items))
        return roll_equipment_reward_items_locked(items, templates=templates, rng=_AlwaysPrefix())

    monkeypatch.setattr(equipment_reward_attachments, "roll_equipment_reward_items_locked", roll)
    assert deliver_campaign_to_manor(campaign, manor)
    assert deliver_campaign_to_manor(campaign, other)
    assert len(calls) == 4  # Separate binding buckets, independently for each recipient.
    assert not deliver_campaign_to_manor(campaign, manor)
    assert len(calls) == 4
    campaign.refresh_from_db()
    assert campaign.attachments == original
    message = Message.objects.get(manor=manor, title=campaign.title)
    variant_key = next(iter(message.attachments["items"]))
    assert variant_key != base.key
    assert message.attachments["bound_items"] == {variant_key: 1}
    monkeypatch.setattr(
        equipment_reward_attachments,
        "roll_equipment_reward_items_locked",
        lambda *args, **kwargs: pytest.fail("claim must preserve generated attachment identity"),
    )
    claim_message_attachments(message)
    item = InventoryItem.objects.get(manor=manor)
    assert item.template.key == variant_key
    assert (item.quantity, item.bound_quantity) == (3, 1)


def test_admin_new_reward_mail_rolls_each_recipient_without_mutating_form(django_user_model, monkeypatch):
    from types import SimpleNamespace

    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from gameplay.admin.messages import MessageAdmin
    from gameplay.models import Message

    first_user = django_user_model.objects.create_user(username="affix_admin_a")
    second_user = django_user_model.objects.create_user(username="affix_admin_b")
    ensure_manor(first_user)
    ensure_manor(second_user)
    base = _equipment()
    _force_roll(monkeypatch, "gameplay.services.equipment_reward_attachments.roll_equipment_reward_items_locked")
    admin = MessageAdmin(Message, AdminSite())
    monkeypatch.setattr(admin, "message_user", lambda *args, **kwargs: None)
    request = RequestFactory().post("/admin/gameplay/message/")
    request.user = first_user
    form = SimpleNamespace(
        cleaned_data={
            "recipients": django_user_model.objects.filter(pk__in=[first_user.pk, second_user.pk]),
            "attachment_items": {base.key: 1},
            "attachment_bound_items": {base.key: 2},
        }
    )
    admin.save_model(request, Message(kind="reward", title="前缀运营奖励"), form, change=False)
    messages = list(Message.objects.filter(title="前缀运营奖励"))
    assert len(messages) == 2
    for message in messages:
        actual_key = next(iter(message.attachments["items"]))
        assert actual_key != base.key
        assert message.attachments["bound_items"] == {actual_key: 2}
    assert form.cleaned_data["attachment_items"] == {base.key: 1}


def test_mission_finalization_reports_actual_variant_reward_counts(django_user_model, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gameplay.services.equipment_affixes import (
        get_or_create_equipment_affix_template,
        roll_equipment_reward_items_locked,
    )
    from gameplay.services.missions_impl.drops import award_mission_drops_locked
    from gameplay.services.missions_impl.finalization_helpers import apply_mission_rewards_if_won

    manor = ensure_manor(django_user_model.objects.create_user(username="affix_mission_report"))
    base = _equipment()
    variant = get_or_create_equipment_affix_template(base, "agility")
    stock = InventoryItem.objects.create(manor=manor, template=variant, quantity=5)
    calls = []

    def roll(items, *, templates=None):
        calls.append(dict(items))
        return roll_equipment_reward_items_locked(items, templates=templates, rng=_AlwaysPrefix())

    monkeypatch.setattr("gameplay.services.missions_impl.drops.roll_equipment_reward_items_locked", roll)
    monkeypatch.setattr("gameplay.services.battle_salvage.calculate_battle_salvage", lambda *args, **kwargs: (0, {}))
    report = SimpleNamespace(winner="attacker", drops={base.key: 2, "silver": 20}, save=Mock())
    run = SimpleNamespace(manor=manor, is_retreating=False, mission=SimpleNamespace(is_defense=False, name="前缀任务"))
    apply_mission_rewards_if_won(
        run,
        report,
        "attacker",
        logger=logging.getLogger(__name__),
        resolve_defense_drops_if_missing=lambda *args: {},
        award_mission_drops_locked=award_mission_drops_locked,
    )
    assert calls == [{base.key: 2}]
    assert report.drops == {variant.key: 2, "silver": 20}
    report.save.assert_called_once_with(update_fields=["drops"])
    stock.refresh_from_db()
    assert stock.quantity == 7
