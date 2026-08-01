from __future__ import annotations

import math

import pytest

from battle.combat_math import effective_attack_value, effective_defense_value, troop_unit_hp
from battle.combatants_pkg import build_troop_combatants
from battle.combatants_pkg.troop_device_bonuses import apply_troop_device_bonus, build_troop_device_bonuses
from battle.execution import BattleOptions, _build_attacker_units
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.battle_snapshots import build_guest_battle_snapshots, build_guest_snapshot_proxies
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.equipment import ensure_inventory_gears, equip_guest


def _create_guest(manor, *, suffix: str) -> Guest:
    template = GuestTemplate.objects.create(
        key=f"device_bonus_guest_tpl_{suffix}",
        name=f"器械门客{suffix}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_attack=100,
        base_intellect=90,
        base_defense=80,
        base_agility=70,
        base_luck=60,
        base_hp=1200,
        default_gender="unknown",
        default_morality=50,
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        level=10,
        force=120,
        intellect=90,
        defense_stat=95,
        agility=80,
        luck=60,
        current_hp=6000,
    )


def _attach_gear(
    guest: Guest,
    *,
    key: str,
    slot: str,
    effect_type: str,
    payload: object,
) -> None:
    ItemTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": key,
            "effect_type": effect_type,
            "effect_payload": payload,
            "rarity": "green",
        },
    )
    template, _ = GearTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": key,
            "slot": slot,
            "rarity": "green",
        },
    )
    GearItem.objects.create(manor=guest.manor, guest=guest, template=template)


@pytest.mark.django_db
def test_build_troop_device_bonuses_accumulates_only_positive_device_payloads(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_agg", password="pass123")
    manor = ensure_manor(user)
    guest_a = _create_guest(manor, suffix="a")
    guest_b = _create_guest(manor, suffix="b")

    _attach_gear(
        guest_a,
        key="equip_device_bonus_a",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={
            "troop_stat_bonus": {
                "qiang": {"hp_flat": 20, "hp_pct": 0.1},
                "bad_class": {"hp_flat": 99},
                "gong": {"attack_pct": -1},
            }
        },
    )
    _attach_gear(
        guest_b,
        key="equip_device_bonus_b",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={
            "troop_stat_bonus": {
                "qiang": {"hp_flat": 5, "defense_flat": 3},
                "gong": {"agility_pct": 0.25},
            }
        },
    )

    bonuses = build_troop_device_bonuses([guest_a, guest_b])

    assert bonuses["qiang"]["hp"] == {"flat": 25, "pct": 0.1}
    assert bonuses["qiang"]["defense"] == {"flat": 3, "pct": 0.0}
    assert bonuses["gong"]["agility"] == {"flat": 0, "pct": 0.25}
    assert "bad_class" not in bonuses
    assert "attack" not in bonuses.get("gong", {})
    assert set(bonuses) == {"qiang", "gong"}
    assert set(bonuses["qiang"]) == {"hp", "defense"}
    assert set(bonuses["gong"]) == {"agility"}


@pytest.mark.django_db
def test_build_troop_device_bonuses_ignores_non_device_and_non_mapping_payloads(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_ignore", password="pass123")
    manor = ensure_manor(user)
    non_device_guest = _create_guest(manor, suffix="non_device")

    _attach_gear(
        non_device_guest,
        key="equip_fake_ornament_bonus",
        slot=GearSlot.ORNAMENT,
        effect_type="equip_ornament",
        payload={"troop_stat_bonus": {"qiang": {"hp_flat": 20}}},
    )
    assert build_troop_device_bonuses([non_device_guest]) == {}

    bad_payload_guest = _create_guest(manor, suffix="bad_payload")

    _attach_gear(
        bad_payload_guest,
        key="equip_bad_device_bonus",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={"troop_stat_bonus": "bad-payload"},
    )
    assert build_troop_device_bonuses([bad_payload_guest]) == {}


@pytest.mark.django_db
def test_build_troop_device_bonuses_accumulates_duplicate_template_keys_per_gear_instance(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_dup_key", password="pass123")
    manor = ensure_manor(user)
    guest_a = _create_guest(manor, suffix="dup_a")
    guest_b = _create_guest(manor, suffix="dup_b")

    payload = {"troop_stat_bonus": {"qiang": {"hp_flat": 12, "hp_pct": 0.05}}}

    _attach_gear(
        guest_a,
        key="equip_shared_device_bonus",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload=payload,
    )
    _attach_gear(
        guest_b,
        key="equip_shared_device_bonus",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload=payload,
    )

    bonuses = build_troop_device_bonuses([guest_a, guest_b])

    assert bonuses == {"qiang": {"hp": {"flat": 24, "pct": 0.1}}}


@pytest.mark.django_db
def test_build_troop_combatants_applies_device_bonus_before_tech():
    troop = build_troop_combatants(
        {"fast_archer": 10},
        side="attacker",
        tech_levels={"gong_hp": 5},
        device_bonuses={"gong": {"hp": {"flat": 20, "pct": 0.5}}},
    )[0]

    assert troop.template_key == "fast_archer"
    assert troop.unit_hp == 75
    assert troop.max_hp == 750
    assert troop.unit_attack == 7


@pytest.mark.django_db
def test_build_troop_combatants_keeps_device_and_tech_fractional_stats_until_boundaries():
    troop = build_troop_combatants(
        {"fast_archer": 201},
        side="attacker",
        tech_levels={
            "gong_attack": 1,
            "gong_defense": 1,
            "gong_agility": 1,
            "gong_hp": 1,
        },
        device_bonuses={
            "gong": {
                "attack": {"flat": 0, "pct": 0.05},
                "defense": {"flat": 0, "pct": 0.05},
                "agility": {"flat": 0, "pct": 0.05},
                "hp": {"flat": 0, "pct": 0.07},
            }
        },
    )[0]

    assert troop.unit_attack == pytest.approx(7 * 1.05 * 1.10)
    assert troop.unit_defense == pytest.approx(2 * 1.05 * 1.10)
    assert troop.unit_hp == pytest.approx(20 * 1.07 * 1.10)
    assert troop.agility == pytest.approx(4 * 1.05 * 1.10)
    assert troop.attack == pytest.approx(troop.unit_attack * 201)
    assert troop.defense == pytest.approx(troop.unit_defense * 201)
    assert troop.max_hp == troop.hp == troop.initial_hp == int(troop.unit_hp * 201)
    assert isinstance(troop.max_hp, int)
    assert troop_unit_hp(troop) == pytest.approx(troop.unit_hp)

    enemy = build_troop_combatants({"fast_archer": 1}, side="defender")[0]
    assert effective_attack_value(troop, enemy) == pytest.approx(troop.unit_attack * 201)
    assert effective_defense_value(troop, enemy) == pytest.approx(troop.unit_defense * max(1.0, math.sqrt(201) / 2.0))


@pytest.mark.parametrize("invalid_value", [float("inf"), float("nan"), 10**1000])
def test_apply_troop_device_bonus_ignores_non_finite_or_overflowing_values(invalid_value):
    assert (
        apply_troop_device_bonus(
            base_value=80,
            troop_class="gong",
            stat="hp",
            device_bonuses={"gong": {"hp": {"flat": invalid_value, "pct": 0}}},
        )
        == 80
    )


@pytest.mark.django_db
def test_real_device_inventory_equip_flow_applies_troop_stat_bonus_in_battle(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_real_flow", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, suffix="real_flow")
    item_template = ItemTemplate.objects.create(
        key="equip_device_bonus_real_flow",
        name="真实器械加成",
        effect_type="equip_device",
        rarity=GuestRarity.GREEN,
        effect_payload={"troop_stat_bonus": {"gong": {"hp_flat": 20, "hp_pct": 0.5}}},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )

    ensure_inventory_gears(manor, slot=GearSlot.DEVICE)

    free_gear = GearItem.objects.select_related("template").get(
        manor=manor,
        guest__isnull=True,
        template__key=item_template.key,
    )
    assert free_gear.template.slot == GearSlot.DEVICE

    equip_guest(free_gear, guest)
    guest.refresh_from_db()
    equipped_gear = guest.gear_items.select_related("template").get(template__key=item_template.key)

    assert equipped_gear.template.slot == GearSlot.DEVICE
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()

    options = BattleOptions(
        troop_loadout={"fast_archer": 10},
        fill_default_troops=False,
        attacker_tech_levels={"gong_hp": 5},
    )

    _, troops = _build_attacker_units([guest], [guest], {"fast_archer": 10}, options, manor)
    troop = troops[0]

    assert troop.template_key == "fast_archer"
    assert troop.unit_hp == 75
    assert troop.max_hp == 750


@pytest.mark.django_db
def test_build_attacker_units_reads_equipped_device_bonuses_from_live_guests(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_wiring", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, suffix="wiring")

    _attach_gear(
        guest,
        key="equip_device_bonus_wiring",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={"troop_stat_bonus": {"gong": {"hp_flat": 20, "hp_pct": 0.5}}},
    )

    options = BattleOptions(
        troop_loadout={"fast_archer": 10},
        fill_default_troops=False,
        attacker_tech_levels={"gong_hp": 5},
    )

    _, troops = _build_attacker_units([guest], [guest], {"fast_archer": 10}, options, manor)
    troop = troops[0]

    assert troop.template_key == "fast_archer"
    assert troop.unit_hp == 75
    assert troop.max_hp == 750


@pytest.mark.django_db
def test_build_attacker_units_ignores_device_bonuses_from_benched_guests(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_bench", password="pass123")
    manor = ensure_manor(user)
    active_guest = _create_guest(manor, suffix="active")
    benched_guest = _create_guest(manor, suffix="benched")

    _attach_gear(
        benched_guest,
        key="equip_device_bonus_benched",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={"troop_stat_bonus": {"gong": {"hp_flat": 20, "hp_pct": 0.5}}},
    )

    options = BattleOptions(
        troop_loadout={"fast_archer": 10},
        fill_default_troops=False,
        attacker_tech_levels={"gong_hp": 5},
        limit=1,
    )

    expected_troop = build_troop_combatants(
        {"fast_archer": 10},
        side="attacker",
        tech_levels={"gong_hp": 5},
    )[0]

    _, troops = _build_attacker_units(
        [active_guest, benched_guest],
        [active_guest],
        {"fast_archer": 10},
        options,
        manor,
    )
    troop = troops[0]

    assert troop.template_key == "fast_archer"
    assert troop.unit_hp == expected_troop.unit_hp
    assert troop.max_hp == expected_troop.max_hp


@pytest.mark.django_db
def test_battle_snapshots_preserve_each_guests_device_bonus_for_replay(django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_snapshot", password="pass123")
    manor = ensure_manor(user)
    guest_a = _create_guest(manor, suffix="snapshot_a")
    guest_b = _create_guest(manor, suffix="snapshot_b")

    _attach_gear(
        guest_a,
        key="equip_device_bonus_snapshot_a",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={"troop_stat_bonus": {"gong": {"hp_flat": 20}}},
    )
    _attach_gear(
        guest_b,
        key="equip_device_bonus_snapshot_b",
        slot=GearSlot.DEVICE,
        effect_type="equip_device",
        payload={"troop_stat_bonus": {"gong": {"hp_pct": 0.5}}},
    )

    snapshots = build_guest_battle_snapshots([guest_a, guest_b], include_identity=True)

    assert snapshots[0]["troop_device_bonuses"] == {"gong": {"hp": {"flat": 20, "pct": 0.0}}}
    assert snapshots[1]["troop_device_bonuses"] == {"gong": {"hp": {"flat": 0, "pct": 0.5}}}

    proxies = build_guest_snapshot_proxies(snapshots, include_guest_identity=True)

    assert build_troop_device_bonuses(proxies) == {"gong": {"hp": {"flat": 20, "pct": 0.5}}}
