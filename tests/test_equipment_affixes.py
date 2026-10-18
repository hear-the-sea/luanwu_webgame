from __future__ import annotations

import copy
import random
from types import SimpleNamespace

import pytest
from django.db import transaction

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.equipment_affixes import (
    AFFIX_LABELS,
    build_equipment_affix_payload,
    equipment_affix_key,
    equipment_can_gain_affix,
    get_or_create_equipment_affix_template,
    grant_items_with_equipment_affixes_locked,
    roll_equipment_reward_items_locked,
)
from gameplay.services.equipment_template_sync import synchronize_equipment_templates
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearTemplate, Guest, GuestTemplate
from guests.services.equipment_payloads import build_gear_template_defaults
from guests.services.equipment_stats import apply_template_stats_to_guest
from guests.utils.equipment_utils import compute_set_bonus


class AlwaysAffixed:
    def random(self):
        return 0.01


class NeverRandom:
    def random(self):
        raise AssertionError("a transferred or excluded item must not be rerolled")


@pytest.fixture
def base_equipment(db):
    return ItemTemplate.objects.create(
        key="affix_test_halberd",
        name="前缀测试戟",
        effect_type="equip_weapon",
        rarity="orange",
        tradeable=True,
        storage_space=200,
        price=10000,
        effect_payload={
            "hp": 240,
            "force": 55,
            "defense": 10,
            "agility": 8,
            "luck": 7,
            "troop_capacity": 70,
            "required_level": 75,
            "set_key": "affix_test_set",
            "set_description": "测试套装",
            "set_bonus": {"pieces": 2, "bonus": {"force": 10}},
        },
    )


@pytest.mark.parametrize("affix", AFFIX_LABELS)
def test_affix_conserves_four_stats_and_preserves_non_convertible_payload(base_equipment, affix):
    original = copy.deepcopy(base_equipment.effect_payload)
    payload = build_equipment_affix_payload(base_equipment, affix)
    assert payload is not None
    stats = ("force", "intellect", "defense", "agility")
    assert sum(payload.get(k, 0) for k in stats) == sum(original.get(k, 0) for k in stats)
    for key in ("hp", "luck", "troop_capacity", "required_level", "set_key", "set_bonus"):
        assert payload[key] == original[key]
    assert base_equipment.effect_payload == original
    assert payload["equipment_base_key"] == base_equipment.key
    assert payload["equipment_base_name"] == base_equipment.name
    assert payload["equipment_affix"] == affix
    assert (
        "未知属性"
        not in ItemTemplate(
            effect_type="equip_weapon",
            effect_payload=payload,
        ).equipment_effect_summary
    )


def test_affix_example_and_integer_rounding(base_equipment):
    payload = build_equipment_affix_payload(base_equipment, "agility")
    assert (payload["force"], payload["defense"], payload["agility"]) == (44, 8, 21)
    base_equipment.effect_payload = {"force": 4, "defense": 4}
    assert all(build_equipment_affix_payload(base_equipment, affix) is None for affix in AFFIX_LABELS)


@pytest.mark.django_db
def test_prefixed_set_items_inherit_set_membership_from_base_templates():
    set_bonus = {"pieces": 2, "bonus": {"force": 25}}
    base_helmet = GearTemplate.objects.create(
        key="xuanwu_helmet",
        name="玄武盔",
        slot="helmet",
        set_key="xuanwu_set",
        set_bonus=set_bonus,
    )
    base_armor = GearTemplate.objects.create(
        key="xuanwu_armor",
        name="玄武甲",
        slot="armor",
        set_key="xuanwu_set",
        set_bonus=set_bonus,
    )
    prefixed_helmet = GearTemplate.objects.create(
        key="affix_force__xuanwu_helmet",
        name="力量之玄武盔",
        slot="helmet",
        base_key=base_helmet.key,
        base_name=base_helmet.name,
    )
    prefixed_armor = GearTemplate.objects.create(
        key="affix_force__xuanwu_armor",
        name="力量之玄武甲",
        slot="armor",
        base_key=base_armor.key,
        base_name=base_armor.name,
    )

    assert compute_set_bonus([SimpleNamespace(template=prefixed_helmet), SimpleNamespace(template=prefixed_armor)]) == {
        "force": 25
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"rarity": "black"},
        {"rarity": "gray"},
        {"effect_type": "equip_device"},
        {"effect_type": "tool"},
        {"key": "soulorn_test"},
        {"key": "equip_baihongkui"},
        {"effect_payload": {"force": 50, "equipment_base_key": "other"}},
    ],
)
def test_excluded_equipment_never_uses_random(base_equipment, overrides):
    for field, value in overrides.items():
        setattr(base_equipment, field, value)
    assert not equipment_can_gain_affix(base_equipment)
    with transaction.atomic():
        quantities, templates = roll_equipment_reward_items_locked(
            {base_equipment.key: 2},
            templates={base_equipment.key: base_equipment},
            rng=NeverRandom(),
        )
    assert quantities == {base_equipment.key: 2}
    assert templates == {base_equipment.key: base_equipment}


def test_batch_probability_quantity_and_uniform_valid_targets(base_equipment):
    with transaction.atomic():
        quantities, _ = roll_equipment_reward_items_locked(
            {base_equipment.key: 10000},
            templates={base_equipment.key: base_equipment},
            rng=random.Random(91),
        )
    assert sum(quantities.values()) == 10000
    assert 2800 <= quantities[base_equipment.key] <= 3200
    for affix in AFFIX_LABELS:
        assert 1500 <= quantities[equipment_affix_key(base_equipment.key, affix)] <= 2000


def test_empty_and_single_attribute_items_only_generate_meaningful_prefixes(base_equipment):
    base_equipment.effect_payload = {"hp": 100, "troop_capacity": 60}
    with transaction.atomic():
        quantities, _ = roll_equipment_reward_items_locked(
            {base_equipment.key: 1},
            templates={base_equipment.key: base_equipment},
            rng=NeverRandom(),
        )
    assert quantities == {base_equipment.key: 1}
    base_equipment.effect_payload = {"force": 40}
    with transaction.atomic():
        quantities, _ = roll_equipment_reward_items_locked(
            {base_equipment.key: 100},
            templates={base_equipment.key: base_equipment},
            rng=AlwaysAffixed(),
        )
    assert base_equipment.key not in quantities
    assert equipment_affix_key(base_equipment.key, "force") not in quantities
    assert sum(quantities.values()) == 100


def test_grants_preserve_exact_bound_counts_and_transfers_do_not_reroll(base_equipment, django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="affix_binding"))
    target = ensure_manor(django_user_model.objects.create_user(username="affix_transfer"))
    with transaction.atomic():
        result = grant_items_with_equipment_affixes_locked(
            manor,
            {base_equipment.key: 8},
            templates={base_equipment.key: base_equipment},
            bound_quantities={base_equipment.key: 5},
            rng=AlwaysAffixed(),
        )
    variant_key = equipment_affix_key(base_equipment.key, "agility")
    assert set(result) == {variant_key}
    item = result[variant_key]
    assert (item.quantity, item.bound_quantity) == (8, 5)
    variant = item.template
    assert (variant.tradeable, variant.price, variant.storage_space) == (True, 10000, 200)
    with transaction.atomic():
        add_item_to_inventory_locked(target, variant.key, 1, template=variant, is_bound=False)
        unchanged, _ = roll_equipment_reward_items_locked(
            {variant.key: 1},
            templates={variant.key: variant},
            rng=NeverRandom(),
        )
    assert unchanged == {variant_key: 1}
    assert InventoryItem.objects.get(manor=target, template=variant).quantity == 1
    assert ItemTemplate.objects.filter(key=variant_key).count() == 1


def test_template_import_refreshes_existing_affix_and_equipped_guest_without_reroll(
    base_equipment,
    django_user_model,
):
    manor = ensure_manor(django_user_model.objects.create_user(username="affix_sync"))
    with transaction.atomic():
        variant = get_or_create_equipment_affix_template(base_equipment, "agility")
    gear_template = GearTemplate.objects.create(
        key=variant.key,
        **build_gear_template_defaults(variant, slot="weapon"),
    )
    guest_template = GuestTemplate.objects.create(key="affix_sync_guest", name="测试门客", base_hp=1000)
    guest = Guest.objects.create(
        manor=manor,
        template=guest_template,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=75,
        current_hp=1000,
    )
    GearItem.objects.create(manor=manor, guest=guest, template=gear_template)
    updates = set()
    apply_template_stats_to_guest(guest, gear_template, 1, updates)
    guest.save(update_fields=list(updates))
    base_equipment.effect_payload = {**base_equipment.effect_payload, "force": 45, "required_level": 80}
    base_equipment.save(update_fields=["effect_payload"])
    synchronize_equipment_templates([base_equipment.key])
    guest.refresh_from_db()
    variant.refresh_from_db()
    gear_template.refresh_from_db()
    assert (guest.force, guest.defense_stat, guest.agility) == (136, 98, 94)
    assert guest.current_hp == 1000
    assert gear_template.required_level == 80
    assert gear_template.base_key == base_equipment.key
    assert variant.effect_payload["equipment_affix"] == "agility"
    assert variant.effect_payload["required_level"] == 80


def test_variant_keys_fit_existing_slug_limit():
    key = equipment_affix_key("x" * 50, "intellect")
    assert len(key) <= 50
    assert key != equipment_affix_key("x" * 49 + "y", "intellect")


@pytest.mark.parametrize("is_bound,default_bound", [(None, True), (True, False), (False, True)])
def test_partial_binding_mapping_uses_normal_inventory_defaults(
    base_equipment, django_user_model, is_bound, default_bound
):
    manor = ensure_manor(django_user_model.objects.create_user(username="affix_default_binding"))
    base_equipment.default_bound = default_bound
    base_equipment.save(update_fields=["default_bound"])
    with transaction.atomic():
        rows = grant_items_with_equipment_affixes_locked(
            manor,
            {base_equipment.key: 3},
            templates={base_equipment.key: base_equipment},
            is_bound=is_bound,
            bound_quantities={},
            rng=AlwaysAffixed(),
        )
    should_bind = default_bound if is_bound is None else is_bound
    assert sum(row.quantity for row in rows.values()) == 3
    assert sum(row.bound_quantity for row in rows.values()) == (3 if should_bind else 0)


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_binding_split_rejects_invalid_total_before_creating_variants(base_equipment, django_user_model, quantity):
    manor = ensure_manor(django_user_model.objects.create_user(username="affix_invalid_binding"))
    with transaction.atomic(), pytest.raises(AssertionError):
        grant_items_with_equipment_affixes_locked(
            manor,
            {base_equipment.key: quantity},
            templates={base_equipment.key: base_equipment},
            bound_quantities={},
            rng=AlwaysAffixed(),
        )
    assert not ItemTemplate.objects.filter(key__startswith="affix_agility__").exists()
