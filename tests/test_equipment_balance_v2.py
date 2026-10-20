"""Approved equipment budgets and safe historical reconciliation."""

import importlib
from itertools import combinations
from types import SimpleNamespace

import pytest
import yaml

migration = importlib.import_module("gameplay.migrations.0198_equipment_balance_and_requirements")


def test_static_config_matches_immutable_approved_snapshot():
    from pathlib import Path

    rows = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())["items"]
    equipment = {row["key"]: row["effect_payload"] for row in rows if row.get("effect_type", "").startswith("equip_")}
    specialization = importlib.import_module("gameplay.migrations.0211_equipment_specialization")
    guild_mounts = importlib.import_module("gameplay.migrations.0214_guild_orange_mounts")
    chiyou = importlib.import_module("gameplay.migrations.0221_chiyou_rewards_and_unique_guests")
    zhaoyun = importlib.import_module("gameplay.migrations.0222_zhaoyun_equipment_rewards")
    feature_equipment = {
        key: item["effect_payload"]
        for migration in (chiyou, zhaoyun)
        for key, item in migration.ITEM_DEFAULTS.items()
        if str(item.get("effect_type", "")).startswith("equip_")
    }
    assert len(equipment) == 278
    assert equipment == {
        **migration.EQUIPMENT_PAYLOADS,
        **specialization.EQUIPMENT_PAYLOADS,
        **guild_mounts.EQUIPMENT_PAYLOADS,
        **feature_equipment,
    }


def test_any_mixed_loadout_with_set_bonuses_adds_at_most_500_troops():
    """Enumerate per-slot choices, retaining each set-count vector's best result."""
    from tests.test_item_template_equipment_balance import SLOT_CAPACITY, _load_item_templates

    items = _load_item_templates()
    equipment = [row for row in items.values() if row.get("effect_type", "").startswith("equip_")]
    sets = {}
    for row in equipment:
        payload = row["effect_payload"]
        definitions = payload.get("set_bonus") or []
        if isinstance(definitions, dict):
            definitions = [definitions]
        for definition in definitions:
            troop_bonus = definition.get("bonus", {}).get("troop_capacity", 0)
            if troop_bonus:
                sets.setdefault(payload["set_key"], []).append((definition["pieces"], troop_bonus))
    sets = {key: sorted(set(definitions)) for key, definitions in sets.items()}
    set_keys = sorted(sets)
    ceilings = [max(pieces for pieces, _ in sets[key]) for key in set_keys]
    states = {tuple(0 for _ in set_keys): 0}
    for slot, capacity in SLOT_CAPACITY.items():
        rows = [row for row in equipment if row["effect_type"] == f"equip_{slot}"]
        options = {}
        for chosen in combinations(rows, min(capacity, len(rows))):
            counts = tuple(sum(row["effect_payload"].get("set_key") == key for row in chosen) for key in set_keys)
            troops = sum(row["effect_payload"].get("troop_capacity", 0) for row in chosen)
            options[counts] = max(options.get(counts, 0), troops)
        next_states = {}
        for current, score in states.items():
            for extra, amount in options.items():
                counts = tuple(min(ceiling, a + b) for a, b, ceiling in zip(current, extra, ceilings))
                next_states[counts] = max(next_states.get(counts, 0), score + amount)
        states = next_states
    maximum = max(
        score + sum(bonus for key, count in zip(set_keys, counts) for required, bonus in sets[key] if count >= required)
        for counts, score in states.items()
    )
    assert maximum == 500


@pytest.mark.parametrize(
    "rarity,old_min,old_max,new_min,new_max,level",
    [
        ("green", 800, 1600, 600, 1000, 30),
        ("blue", 1500, 2500, 1000, 1600, 40),
        ("purple", 2600, 3500, 1600, 2200, 55),
    ],
)
def test_soul_roll_mapping_preserves_position_secondary_stats_and_reruns(
    rarity, old_min, old_max, new_min, new_max, level
):
    for original_hp, expected in [
        (old_min, new_min),
        (old_max, new_max),
        ((old_min + old_max) // 2, (new_min + new_max) // 2),
    ]:
        original = {
            "hp": original_hp,
            "force": 19,
            "intellect": 22,
            "agility": 21,
            "luck": 18,
        }
        result = migration._soul_payload(original, rarity)
        assert result == {**original, "hp": expected, "required_level": level}
        assert migration._soul_payload(result, rarity) == result
        assert original["hp"] == original_hp


@pytest.mark.django_db
def test_balance_migration_reconciles_worn_set_and_soul_without_touching_naked_stats(
    django_user_model,
):
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate
    from guests.models import GearItem, GearTemplate
    from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest
    from tests.test_equipment_template_sync import _create_guest

    manor, guest = _create_guest(django_user_model, "balance_v2")
    guest.level = 1
    guest.current_hp = 99999
    guest.save(update_fields=["level", "current_hp"])
    naked = {
        field: getattr(guest, field)
        for field in set(migration.STAT_FIELDS.values()) | {"attack_bonus", "defense_bonus"}
    }
    before = {
        "hp": 500,
        "force": 10,
        "defense": 20,
        "troop_capacity": 100,
        "set_key": "xiaowei_set",
        "set_description": "校尉套装",
        "set_bonus": {
            "pieces": 2,
            "bonus": {"force": 50, "defense": 20, "troop_capacity": 100},
        },
    }
    gear_ids = []
    for key, slot in [
        ("equip_xiaoweitoukui", "helmet"),
        ("equip_xiaoweikaijia", "armor"),
        ("equip_xiaoweichangxue", "shoes"),
        ("equip_xiaoweichangjian", "weapon"),
    ]:
        ItemTemplate.objects.create(
            key=key,
            name=key,
            effect_type=f"equip_{slot}",
            rarity="blue",
            effect_payload=before,
        )
        template = GearTemplate.objects.create(
            key=key,
            name=key,
            slot=slot,
            rarity="blue",
            extra_stats={stat: before[stat] for stat in migration.STAT_FIELDS if stat in before},
            set_key="xiaowei_set",
            set_description=before["set_description"],
            set_bonus=before["set_bonus"],
        )
        gear = GearItem.objects.create(manor=manor, guest=guest, template=template)
        gear_ids.append(gear.pk)
        updates = set()
        apply_template_stats_to_guest(guest, template, 1, updates)
        guest.save(update_fields=list(updates))
    soul = ItemTemplate.objects.create(
        key="soulorn_balance_v2",
        name="魂饰",
        effect_type="equip_ornament",
        rarity="purple",
        effect_payload={
            "hp": 3050,
            "force": 30,
            "intellect": 20,
            "agility": 25,
            "luck": 15,
        },
    )
    soul_gear = GearTemplate.objects.create(
        key=soul.key,
        name=soul.name,
        slot="ornament",
        rarity="purple",
        extra_stats=soul.effect_payload,
    )
    GearItem.objects.create(manor=manor, guest=guest, template=soul_gear)
    updates = set()
    apply_template_stats_to_guest(guest, soul_gear, 1, updates)
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)
    unrelated = ItemTemplate.objects.create(
        key="balance_v2_resource",
        name="资源",
        effect_type="resource",
        effect_payload={"amount": 123},
    )
    count = GearTemplate.objects.count()

    migration.apply_equipment_balance(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    soul.refresh_from_db()
    assert GearTemplate.objects.count() == count
    assert (
        list(GearItem.objects.filter(pk__in=gear_ids, guest=guest).order_by("pk").values_list("pk", flat=True))
        == gear_ids
    )
    assert guest.level == 1
    assert soul.effect_payload["hp"] == 1900
    assert soul.effect_payload["required_level"] == 55
    all_gears = list(guest.gear_items.select_related("template"))
    for field, bare in naked.items():
        expected = bare
        for gear in all_gears:
            for stat, target in migration.STAT_FIELDS.items():
                if target == field:
                    expected += gear.template.extra_stats.get(stat, 0)
            if field in ("attack_bonus", "defense_bonus"):
                expected += getattr(gear.template, field)
        for stat, target in migration.SET_FIELDS.items():
            if target == field:
                expected += guest.gear_set_bonus.get(stat, 0)
        assert getattr(guest, field) == expected, field
    assert guest.current_hp == guest.max_hp
    unrelated.refresh_from_db()
    assert unrelated.effect_payload == {"amount": 123}
    first = {field: getattr(guest, field) for field in naked}
    guest.current_hp = 10
    guest.save(update_fields=["current_hp"])
    migration.apply_equipment_balance(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    assert {field: getattr(guest, field) for field in naked} == first
    assert guest.current_hp == 10
