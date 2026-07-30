from __future__ import annotations

from datetime import timedelta
from itertools import count

import pytest
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.virtual_player_core.maintenance_action_specs import EquipmentEquipActionSpec
from gameplay.services.virtual_player_core.maintenance_candidates import build_equipment_equip_candidates
from gameplay.services.virtual_player_core.projection import StrengthSummary, calculate_guest_arena_power
from gameplay.services.virtual_player_core.strategy import BotDevelopmentPlan
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestStatus,
    GuestTemplate,
)
from guests.services.equipment import equip_guest

_COUNTER = count(1)


def _key(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


def _config() -> dict[str, object]:
    return {
        "projection": {
            "gear_max_rarity_by_stage": {
                1: "green",
                7: "blue",
                11: "purple",
                16: "orange",
            }
        }
    }


def _plan(**overrides: object) -> BotDevelopmentPlan:
    values: dict[str, object] = {
        "schema_version": 1,
        "optimization_bias": 0.75,
        "inertia_bias": 0.4,
        "roster_focus": 0.8,
        "preferred_guest_archetypes": ("civil", "military"),
        "primary_troop_class": "dao",
        "secondary_troop_class": "qiang",
        "troop_mix": (("dao", 0.7), ("qiang", 0.3)),
        "preferred_gear_stats": ("force",),
        "preferred_skill_kinds": ("active",),
        "building_focuses": ("farm",),
        "technology_focuses": ("farming",),
    }
    values.update(overrides)
    return BotDevelopmentPlan(**values)  # type: ignore[arg-type]


def _create_guest(
    manor: Manor,
    *,
    archetype: str = GuestArchetype.MILITARY,
    status: str = GuestStatus.IDLE,
    training_complete_at=None,
    force: int = 100,
    intellect: int = 80,
    defense: int = 60,
    hp_bonus: int = 0,
) -> Guest:
    template = GuestTemplate.objects.create(
        key=_key("maintenance_equipment_guest"),
        name="Maintenance equipment guest",
        archetype=archetype,
        rarity=GuestRarity.GREEN,
        base_hp=1_000,
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        force=force,
        intellect=intellect,
        defense_stat=defense,
        hp_bonus=hp_bonus,
        agility=50,
        luck=40,
        status=status,
        training_complete_at=training_complete_at,
    )


def _create_inventory_gear(
    manor: Manor,
    *,
    key: str | None = None,
    name: str = "Candidate gear",
    effect_type: str = "equip_weapon",
    payload: dict[str, object] | None = None,
    rarity: str = GuestRarity.GREEN,
    quantity: int = 1,
    price: int = 0,
) -> InventoryItem:
    template = ItemTemplate.objects.create(
        key=key or _key("maintenance_equipment_item"),
        name=name,
        effect_type=effect_type,
        effect_payload=payload or {},
        rarity=rarity,
        price=price,
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


def _equip_existing(
    manor: Manor,
    guest: Guest,
    *,
    name: str,
    slot: str,
    extra_stats: dict[str, int] | None = None,
    set_key: str = "",
    set_bonus: dict[str, object] | list[dict[str, object]] | None = None,
) -> GearItem:
    template = GearTemplate.objects.create(
        key=_key("maintenance_equipment_existing"),
        name=name,
        slot=slot,
        rarity=GuestRarity.GREEN,
        set_key=set_key,
        set_bonus=set_bonus or {},
        extra_stats=extra_stats or {},
    )
    gear = GearItem.objects.create(manor=manor, template=template)
    equip_guest(gear, guest)
    guest.refresh_from_db()
    return GearItem.objects.select_related("template").get(pk=gear.pk)


def _guest_power(guest: Guest) -> int:
    return calculate_guest_arena_power(
        force=int(guest.force),
        intellect=int(guest.intellect),
        defense=int(guest.defense_stat),
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
    )


def _strength(guest: Guest, *, lineup_padding: int = 75) -> StrengthSummary:
    lineup_power = _guest_power(guest) + lineup_padding
    troop_total = 20
    return StrengthSummary(
        composite=float(lineup_power + 2 * troop_total),
        components={
            "arena_lineup_power": lineup_power,
            "core_building_level": 4,
            "guest_count": 1,
            "max_guest_level": int(guest.level),
            "prestige": 90,
            "troop_total": troop_total,
        },
    )


def _snapshots(
    manor: Manor,
) -> tuple[tuple[Guest, ...], tuple[GearItem, ...], tuple[InventoryItem, ...]]:
    return (
        tuple(Guest.objects.filter(manor=manor).select_related("template").order_by("id")),
        tuple(GearItem.objects.filter(manor=manor, guest__isnull=False).select_related("template").order_by("id")),
        tuple(InventoryItem.objects.filter(manor=manor).select_related("template").order_by("template__key", "id")),
    )


@pytest.mark.django_db
def test_empty_slot_builds_exact_spec_and_uses_snapshots_without_queries(
    manor_factory,
    django_assert_num_queries,
) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor)
    item = _create_inventory_gear(
        manor,
        key=_key("candidate_weapon"),
        payload={"force": 40},
        quantity=2,
        price=100,
    )
    guests, gear_items, warehouse_items = _snapshots(manor)
    guest_snapshot = guests[0]
    strength_before = _strength(guest_snapshot)

    with django_assert_num_queries(0):
        candidates, specs = build_equipment_equip_candidates(
            manor_id=int(manor.id),
            prestige_band="newbie",
            strength_before=strength_before,
            development_plan=_plan(),
            growth_stage=1,
            config=_config(),
            guests=guests,
            gear_items=gear_items,
            warehouse_items=warehouse_items,
        )

    assert len(candidates) == 1
    intent = candidates[0]
    spec = specs[intent.business_key]
    assert isinstance(spec, EquipmentEquipActionSpec)
    assert spec == EquipmentEquipActionSpec(
        guest_id=int(guest.id),
        inventory_item_id=int(item.id),
        item_template_id=int(item.template_id),
        item_key=str(item.template.key),
        item_quantity_before=2,
        slot=GearSlot.WEAPON,
    )
    expected_guest_power = calculate_guest_arena_power(
        force=int(guest_snapshot.force) + 40,
        intellect=int(guest_snapshot.intellect),
        defense=int(guest_snapshot.defense_stat),
        hp_bonus=int(guest_snapshot.hp_bonus),
        archetype=str(guest_snapshot.template.archetype),
        base_hp=int(guest_snapshot.template.base_hp),
    )
    expected_gain = expected_guest_power - _guest_power(guest_snapshot)
    assert intent.strength_after.components["arena_lineup_power"] == (
        strength_before.components["arena_lineup_power"] + expected_gain
    )
    for component in strength_before.components:
        if component != "arena_lineup_power":
            assert intent.strength_after.components[component] == strength_before.components[component]
    assert intent.strength_after.composite == (
        intent.strength_after.components["arena_lineup_power"] + 2 * strength_before.components["troop_total"]
    )


@pytest.mark.django_db
def test_single_capacity_replacement_removes_old_direct_stats_and_weakens_never(
    manor_factory,
) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor, force=100)
    _equip_existing(
        manor,
        guest,
        name="Old weapon",
        slot=GearSlot.WEAPON,
        extra_stats={"force": 20},
    )
    strong = _create_inventory_gear(
        manor,
        key=_key("strong_weapon"),
        name="Strong weapon",
        payload={"force": 100},
    )
    weak = _create_inventory_gear(
        manor,
        key=_key("weak_weapon"),
        name="Weak weapon",
        payload={"force": 5},
    )
    guests, gear_items, warehouse_items = _snapshots(manor)
    guest_snapshot = guests[0]
    strength_before = _strength(guest_snapshot)

    candidates, specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=strength_before,
        development_plan=_plan(inertia_bias=0.0),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert [specs[candidate.business_key].inventory_item_id for candidate in candidates] == [int(strong.id)]
    assert all(specs[candidate.business_key].inventory_item_id != int(weak.id) for candidate in candidates)
    expected_power = calculate_guest_arena_power(
        force=200,
        intellect=int(guest_snapshot.intellect),
        defense=int(guest_snapshot.defense_stat),
        hp_bonus=int(guest_snapshot.hp_bonus),
        archetype=str(guest_snapshot.template.archetype),
        base_hp=int(guest_snapshot.template.base_hp),
    )
    assert candidates[0].strength_after.components["arena_lineup_power"] == (
        strength_before.components["arena_lineup_power"] - _guest_power(guest_snapshot) + expected_power
    )


@pytest.mark.django_db
def test_set_activation_projects_exact_power_without_treating_set_defense_as_defense_stat(
    manor_factory,
) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor, force=100, defense=60)
    set_bonus = {
        "pieces": 2,
        "bonus": {"force": 30, "defense": 500},
    }
    _equip_existing(
        manor,
        guest,
        name="Set helmet",
        slot=GearSlot.HELMET,
        set_key="projection_set",
        set_bonus=set_bonus,
    )
    _create_inventory_gear(
        manor,
        name="Set armor",
        effect_type="equip_armor",
        payload={
            "set_key": "projection_set",
            "set_bonus": set_bonus,
        },
    )
    guests, gear_items, warehouse_items = _snapshots(manor)
    guest_snapshot = guests[0]
    strength_before = _strength(guest_snapshot)

    candidates, _specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=strength_before,
        development_plan=_plan(),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert len(candidates) == 1
    expected_power = calculate_guest_arena_power(
        force=130,
        intellect=int(guest_snapshot.intellect),
        defense=60,
        hp_bonus=int(guest_snapshot.hp_bonus),
        archetype=str(guest_snapshot.template.archetype),
        base_hp=int(guest_snapshot.template.base_hp),
    )
    assert candidates[0].strength_after.components["arena_lineup_power"] == (
        strength_before.components["arena_lineup_power"] - _guest_power(guest_snapshot) + expected_power
    )


@pytest.mark.django_db
def test_replacement_projects_set_loss_before_adding_new_direct_stats(
    manor_factory,
) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor, force=100)
    set_bonus = {"pieces": 2, "bonus": {"force": 30}}
    _equip_existing(
        manor,
        guest,
        name="Set helmet",
        slot=GearSlot.HELMET,
        set_key="lost_set",
        set_bonus=set_bonus,
    )
    _equip_existing(
        manor,
        guest,
        name="Set weapon",
        slot=GearSlot.WEAPON,
        set_key="lost_set",
        set_bonus=set_bonus,
    )
    _create_inventory_gear(
        manor,
        name="Replacement weapon",
        payload={"force": 150},
    )
    guests, gear_items, warehouse_items = _snapshots(manor)
    guest_snapshot = guests[0]
    assert guest_snapshot.force == 130
    assert guest_snapshot.gear_set_bonus == {"force": 30}
    strength_before = _strength(guest_snapshot)

    candidates, _specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=strength_before,
        development_plan=_plan(inertia_bias=0.0),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert len(candidates) == 1
    expected_power = calculate_guest_arena_power(
        force=250,
        intellect=int(guest_snapshot.intellect),
        defense=int(guest_snapshot.defense_stat),
        hp_bonus=int(guest_snapshot.hp_bonus),
        archetype=str(guest_snapshot.template.archetype),
        base_hp=int(guest_snapshot.template.base_hp),
    )
    assert candidates[0].strength_after.components["arena_lineup_power"] == (
        strength_before.components["arena_lineup_power"] - _guest_power(guest_snapshot) + expected_power
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("slot", "effect_type"),
    [
        (GearSlot.DEVICE, "equip_device"),
        (GearSlot.ORNAMENT, "equip_ornament"),
    ],
)
def test_multi_capacity_slots_are_filtered_when_full(
    manor_factory,
    slot: str,
    effect_type: str,
) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor)
    for index in range(3):
        _equip_existing(
            manor,
            guest,
            name=f"Existing {slot} {index}",
            slot=slot,
            extra_stats={"agility": 1},
        )
    _create_inventory_gear(
        manor,
        name=f"Candidate {slot}",
        effect_type=effect_type,
        payload={"force": 100},
    )
    guests, gear_items, warehouse_items = _snapshots(manor)

    candidates, specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=_strength(guests[0]),
        development_plan=_plan(),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert candidates == ()
    assert specs == {}


@pytest.mark.django_db
def test_non_idle_and_training_guests_are_filtered(manor_factory) -> None:
    manor, _user = manor_factory()
    working = _create_guest(manor, status=GuestStatus.WORKING)
    training = _create_guest(
        manor,
        training_complete_at=timezone.now() + timedelta(hours=1),
    )
    _create_inventory_gear(manor, payload={"force": 100})
    guests, gear_items, warehouse_items = _snapshots(manor)

    candidates, specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=_strength(next(guest for guest in guests if guest.id == training.id)),
        development_plan=_plan(),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert working.id != training.id
    assert candidates == ()
    assert specs == {}


@pytest.mark.django_db
def test_duplicate_name_and_stage_rarity_cap_are_filtered(manor_factory) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor)
    _equip_existing(
        manor,
        guest,
        name="Duplicate device",
        slot=GearSlot.DEVICE,
    )
    _create_inventory_gear(
        manor,
        name="Duplicate device",
        effect_type="equip_device",
        payload={"force": 100},
    )
    _create_inventory_gear(
        manor,
        name="Too rare weapon",
        payload={"force": 100},
        rarity=GuestRarity.BLUE,
    )
    guests, gear_items, warehouse_items = _snapshots(manor)

    candidates, specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=_strength(guests[0]),
        development_plan=_plan(),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert candidates == ()
    assert specs == {}


@pytest.mark.django_db
def test_swap_inertia_uses_eight_to_fifteen_percent_threshold(manor_factory) -> None:
    manor, _user = manor_factory()
    guest = _create_guest(manor)
    _equip_existing(
        manor,
        guest,
        name="Threshold old weapon",
        slot=GearSlot.WEAPON,
    )
    guest_snapshot = Guest.objects.select_related("template").get(pk=guest.pk)
    before_power = _guest_power(guest_snapshot)
    force_gain = next(
        gain
        for gain in range(1, 500)
        if 0.09
        <= (
            calculate_guest_arena_power(
                force=int(guest_snapshot.force) + gain,
                intellect=int(guest_snapshot.intellect),
                defense=int(guest_snapshot.defense_stat),
                hp_bonus=int(guest_snapshot.hp_bonus),
                archetype=str(guest_snapshot.template.archetype),
                base_hp=int(guest_snapshot.template.base_hp),
            )
            - before_power
        )
        / before_power
        < 0.14
    )
    _create_inventory_gear(
        manor,
        name="Threshold candidate weapon",
        payload={"force": force_gain},
    )
    guests, gear_items, warehouse_items = _snapshots(manor)
    strength_before = _strength(guests[0])

    low_inertia, _low_specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=strength_before,
        development_plan=_plan(inertia_bias=0.0),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )
    high_inertia, high_specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band="newbie",
        strength_before=strength_before,
        development_plan=_plan(inertia_bias=1.0),
        growth_stage=1,
        config=_config(),
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )

    assert len(low_inertia) == 1
    assert high_inertia == ()
    assert high_specs == {}


@pytest.mark.django_db
def test_candidate_order_and_utility_are_deterministic_and_role_aware(
    manor_factory,
) -> None:
    manor, _user = manor_factory()
    military = _create_guest(manor, archetype=GuestArchetype.MILITARY)
    civil = _create_guest(manor, archetype=GuestArchetype.CIVIL)
    force_item = _create_inventory_gear(
        manor,
        key=_key("a_force_weapon"),
        name="Force weapon",
        payload={"force": 30},
        quantity=5,
    )
    intellect_item = _create_inventory_gear(
        manor,
        key=_key("b_intellect_weapon"),
        name="Intellect weapon",
        payload={"intellect": 30},
        quantity=1,
        price=5_000,
    )
    guests, gear_items, warehouse_items = _snapshots(manor)
    strength_before = _strength(guests[0])
    kwargs = {
        "manor_id": int(manor.id),
        "prestige_band": "newbie",
        "strength_before": strength_before,
        "development_plan": _plan(preferred_gear_stats=("force",)),
        "growth_stage": 1,
        "config": _config(),
        "gear_items": tuple(reversed(gear_items)),
    }

    first = build_equipment_equip_candidates(
        **kwargs,
        guests=tuple(reversed(guests)),
        warehouse_items=tuple(reversed(warehouse_items)),
    )
    second = build_equipment_equip_candidates(
        **kwargs,
        guests=guests,
        warehouse_items=warehouse_items,
    )

    assert first == second
    candidates, specs = first
    assert [candidate.business_key for candidate in candidates] == [
        f"equipment_equip:guest:{military.id}:item:{force_item.template.key}",
        f"equipment_equip:guest:{military.id}:item:{intellect_item.template.key}",
        f"equipment_equip:guest:{civil.id}:item:{force_item.template.key}",
        f"equipment_equip:guest:{civil.id}:item:{intellect_item.template.key}",
    ]
    utilities = {candidate.business_key: candidate.utility_score for candidate in candidates}
    military_force_key = f"equipment_equip:guest:{military.id}:item:{force_item.template.key}"
    military_intellect_key = f"equipment_equip:guest:{military.id}:item:{intellect_item.template.key}"
    civil_force_key = f"equipment_equip:guest:{civil.id}:item:{force_item.template.key}"
    assert utilities[military_force_key] > utilities[military_intellect_key]
    assert utilities[military_force_key] > utilities[civil_force_key]
    assert set(specs) == set(utilities)
