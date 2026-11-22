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
    armor_luck = importlib.import_module("gameplay.migrations.0230_chiyou_armor_luck")
    calibration_275 = importlib.import_module("gameplay.migrations.0275_equipment_balance_calibration")
    lvbu_276 = importlib.import_module("gameplay.migrations.0276_lvbu_equipment_set")
    lvbu_277 = importlib.import_module("gameplay.migrations.0277_lvbu_set_concise_effect_description")
    luck_278 = importlib.import_module("gameplay.migrations.0278_equipment_luck_progression")
    callback_279 = importlib.import_module("gameplay.migrations.0279_equipment_callback_rebalance")
    feature_equipment = {
        key: item["effect_payload"]
        for migration in (chiyou, zhaoyun)
        for key, item in migration.ITEM_DEFAULTS.items()
        if str(item.get("effect_type", "")).startswith("equip_")
    }
    for key, overrides in armor_luck.EQUIPMENT_PAYLOAD_OVERRIDES.items():
        feature_equipment[key] = {**feature_equipment[key], **overrides}
    expected = {
        **migration.EQUIPMENT_PAYLOADS,
        **specialization.EQUIPMENT_PAYLOADS,
        **guild_mounts.EQUIPMENT_PAYLOADS,
        **feature_equipment,
        **calibration_275.EQUIPMENT_PAYLOADS,
        **lvbu_276.EQUIPMENT_PAYLOADS,
        **lvbu_277.EQUIPMENT_PAYLOADS,
    }
    for key, overrides in luck_278.EQUIPMENT_PAYLOAD_OVERRIDES.items():
        expected[key] = {**expected[key], **overrides}
    expected.update(callback_279.EQUIPMENT_PAYLOADS)
    assert len(equipment) == 280
    assert equipment == expected


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
    assert maximum <= 500


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


@pytest.mark.django_db
def test_0275_equipment_balance_calibration_reconciles_worn_affix_and_set_idempotently(
    django_user_model,
):
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate
    from guests.models import GearItem, GearTemplate
    from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest
    from tests.test_equipment_template_sync import _create_guest

    calibration_275 = importlib.import_module("gameplay.migrations.0275_equipment_balance_calibration")
    manor, guest = _create_guest(django_user_model, "cal_0275_a")
    guest.level = 20
    guest.current_hp = 99999
    guest.save(update_fields=["level", "current_hp"])
    naked = {
        field: getattr(guest, field)
        for field in set(calibration_275.STAT_FIELDS.values()) | {"attack_bonus", "defense_bonus"}
    }

    old_xiaowei_bonus = {
        "pieces": 4,
        "bonus": {"hp": 100, "force": 15, "troop_capacity": 20},
    }
    xiaowei_items = [
        (
            "equip_xiaoweitoukui",
            "helmet",
            {
                "hp": 300,
                "defense": 20,
                "force": 15,
                "troop_capacity": 15,
                "set_key": "xiaowei_set",
                "set_description": "校尉套装",
                "set_bonus": old_xiaowei_bonus,
                "required_level": 30,
            },
        ),
        (
            "equip_xiaoweikaijia",
            "armor",
            {
                "hp": 400,
                "defense": 25,
                "force": 10,
                "troop_capacity": 20,
                "set_key": "xiaowei_set",
                "set_description": "校尉套装",
                "set_bonus": old_xiaowei_bonus,
                "required_level": 30,
            },
        ),
        (
            "equip_xiaoweichangxue",
            "shoes",
            {
                "hp": 200,
                "defense": 14,
                "force": 5,
                "agility": 20,
                "troop_capacity": 10,
                "set_key": "xiaowei_set",
                "set_description": "校尉套装",
                "set_bonus": old_xiaowei_bonus,
                "required_level": 30,
            },
        ),
        (
            "equip_xiaoweichangjian",
            "weapon",
            {
                "hp": 72,
                "force": 45,
                "troop_capacity": 25,
                "set_key": "xiaowei_set",
                "set_description": "校尉套装",
                "set_bonus": old_xiaowei_bonus,
                "required_level": 30,
            },
        ),
    ]

    for key, slot, payload in xiaowei_items:
        base_item, _ = ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "effect_type": f"equip_{slot}",
                "rarity": "blue",
                "effect_payload": payload,
            },
        )
        base_tpl, _ = GearTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "slot": slot,
                "rarity": "blue",
                "base_key": key,
                "base_name": key,
                "extra_stats": {s: payload[s] for s in calibration_275.STAT_FIELDS if s in payload},
                "set_key": "xiaowei_set",
                "set_description": "校尉套装",
                "set_bonus": old_xiaowei_bonus,
            },
        )
        if key == "equip_xiaoweichangjian":
            affix_key = calibration_275._affix_key(key, "force", 50)
            affix_payload = calibration_275._affix_payload(base_item, "force")
            ItemTemplate.objects.update_or_create(
                key=affix_key,
                defaults={
                    "name": f"强攻·{key}",
                    "effect_type": f"equip_{slot}",
                    "rarity": "blue",
                    "effect_payload": affix_payload,
                },
            )
            worn_tpl, _ = GearTemplate.objects.update_or_create(
                key=affix_key,
                defaults={
                    "name": f"强攻·{key}",
                    "slot": slot,
                    "rarity": "blue",
                    "base_key": key,
                    "base_name": key,
                    "extra_stats": {s: affix_payload[s] for s in calibration_275.STAT_FIELDS if s in affix_payload},
                    "set_key": "xiaowei_set",
                    "set_description": "校尉套装",
                    "set_bonus": old_xiaowei_bonus,
                },
            )
        else:
            worn_tpl = base_tpl
        GearItem.objects.create(manor=manor, guest=guest, template=worn_tpl)
        updates: set[str] = set()
        apply_template_stats_to_guest(guest, worn_tpl, 1, updates)
        guest.save(update_fields=list(updates))

    # 佩戴一件下调兵力的传国玉玺、一件上调属性的不死鸟羽、以及一件灵魂容器高阶饰品。
    old_yuxi = {
        "hp": 300,
        "defense": 5,
        "force": 16,
        "intellect": 16,
        "troop_capacity": 160,
        "luck": 15,
        "required_level": 80,
    }
    old_niaoyu = {
        "hp": 280,
        "force": 34,
        "agility": 14,
        "required_level": 65,
    }
    soul_payload = {
        "hp": 2050,
        "force": 96,
        "intellect": 92,
        "agility": 94,
        "luck": 98,
        "required_level": 55,
    }
    for key, rarity, payload in [
        ("equip_chuanguoyuxi", "orange", old_yuxi),
        ("equip_businiaoyu", "orange", old_niaoyu),
        ("soulorn_0275_test", "orange", soul_payload),
    ]:
        ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "effect_type": "equip_ornament",
                "rarity": rarity,
                "effect_payload": payload,
            },
        )
        tpl, _ = GearTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "slot": "ornament",
                "rarity": rarity,
                "base_key": key,
                "base_name": key,
                "extra_stats": {s: payload[s] for s in calibration_275.STAT_FIELDS if s in payload},
            },
        )
        GearItem.objects.create(manor=manor, guest=guest, template=tpl)
        updates = set()
        apply_template_stats_to_guest(guest, tpl, 1, updates)
        guest.save(update_fields=list(updates))

    apply_set_bonuses(guest)

    # 另一位门客佩戴词缀版踏天靴（属性上调+词缀重算）和蚩尤甲（保持高端不被修改）。
    manor2, guest2 = _create_guest(django_user_model, "cal_0275_b")
    guest2.level = 30
    guest2.current_hp = 99999
    guest2.save(update_fields=["level", "current_hp"])
    naked2 = {
        field: getattr(guest2, field)
        for field in set(calibration_275.STAT_FIELDS.values()) | {"attack_bonus", "defense_bonus"}
    }
    old_tatianxue = {
        "hp": 250,
        "defense": 10,
        "force": 22,
        "intellect": 0,
        "agility": 33,
        "required_level": 70,
    }
    chiyou_payload = {
        "hp": 600,
        "defense": 48,
        "force": 40,
        "intellect": 0,
        "luck": 15,
        "required_level": 80,
    }
    tatian_base, _ = ItemTemplate.objects.update_or_create(
        key="equip_tatianxue",
        defaults={
            "name": "踏天靴",
            "effect_type": "equip_shoes",
            "rarity": "orange",
            "effect_payload": old_tatianxue,
        },
    )
    tatian_affix_key = calibration_275._affix_key("equip_tatianxue", "defense", 50)
    tatian_affix_old = calibration_275._affix_payload(tatian_base, "defense")
    ItemTemplate.objects.update_or_create(
        key=tatian_affix_key,
        defaults={
            "name": "固守·踏天靴",
            "effect_type": "equip_shoes",
            "rarity": "orange",
            "effect_payload": tatian_affix_old,
        },
    )
    tatian_affix_tpl, _ = GearTemplate.objects.update_or_create(
        key=tatian_affix_key,
        defaults={
            "name": "固守·踏天靴",
            "slot": "shoes",
            "rarity": "orange",
            "base_key": "equip_tatianxue",
            "base_name": "踏天靴",
            "extra_stats": {s: tatian_affix_old[s] for s in calibration_275.STAT_FIELDS if s in tatian_affix_old},
        },
    )
    ItemTemplate.objects.update_or_create(
        key="equip_chiyoujia",
        defaults={
            "name": "蚩尤甲",
            "effect_type": "equip_armor",
            "rarity": "orange",
            "effect_payload": chiyou_payload,
        },
    )
    chiyou_tpl, _ = GearTemplate.objects.update_or_create(
        key="equip_chiyoujia",
        defaults={
            "name": "蚩尤甲",
            "slot": "armor",
            "rarity": "orange",
            "base_key": "equip_chiyoujia",
            "base_name": "蚩尤甲",
            "extra_stats": {s: chiyou_payload[s] for s in calibration_275.STAT_FIELDS if s in chiyou_payload},
        },
    )
    for tpl in (tatian_affix_tpl, chiyou_tpl):
        GearItem.objects.create(manor=manor2, guest=guest2, template=tpl)
        updates = set()
        apply_template_stats_to_guest(guest2, tpl, 1, updates)
        guest2.save(update_fields=list(updates))

    calibration_275.apply_equipment_balance_calibration(apps, SimpleNamespace(connection=connection))

    guest.refresh_from_db()
    guest2.refresh_from_db()
    assert ItemTemplate.objects.get(key="soulorn_0275_test").effect_payload == soul_payload
    assert ItemTemplate.objects.get(key="equip_chiyoujia").effect_payload == {
        "hp": 640,
        "defense": 48,
        "force": 36,
        "intellect": 12,
        "agility": 14,
        "luck": 15,
        "required_level": 80,
    }
    assert ItemTemplate.objects.get(key="equip_chuanguoyuxi").effect_payload["troop_capacity"] == 92
    assert guest.gear_set_bonus == {"hp": 220, "defense": 8, "force": 22, "troop_capacity": 30}

    # 验证词缀版踏天靴同步了重算后的四维转换。
    tatian_affix_tpl.refresh_from_db()
    assert tatian_affix_tpl.extra_stats == {
        "hp": 280,
        "defense": 12 + (36 // 5) + (30 // 5),
        "force": 36 - (36 // 5),
        "agility": 30 - (30 // 5),
        "luck": 6,
    }

    for g, bare_map in ((guest, naked), (guest2, naked2)):
        worn = list(g.gear_items.select_related("template"))
        for field, bare in bare_map.items():
            expected = bare
            for gear in worn:
                for stat, target in calibration_275.STAT_FIELDS.items():
                    if target == field:
                        expected += gear.template.extra_stats.get(stat, 0)
                if field in ("attack_bonus", "defense_bonus"):
                    expected += getattr(gear.template, field)
            for stat, target in calibration_275.SET_FIELDS.items():
                if target == field:
                    expected += g.gear_set_bonus.get(stat, 0)
            assert getattr(g, field) == expected, f"{g.pk}:{field}"
        assert g.current_hp == g.max_hp

    # 验证幂等性与不治疗受伤门客。
    snapshot = {field: getattr(guest, field) for field in naked}
    guest.current_hp = 10
    guest.save(update_fields=["current_hp"])
    calibration_275.apply_equipment_balance_calibration(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    assert {field: getattr(guest, field) for field in naked} == snapshot
    assert guest.current_hp == 10


def test_equipment_catalog_maintains_archetype_differentiation():
    """同槽位同品质常规装备保持清晰流派分化，杜绝高相似度换皮。"""
    import math
    from collections import defaultdict

    from tests.test_item_template_equipment_balance import _load_item_templates

    non_hp_stats = ("defense", "force", "intellect", "agility", "luck", "troop_capacity")
    items = _load_item_templates()
    groups = defaultdict(list)
    for key, row in items.items():
        effect_type = str(row.get("effect_type") or "")
        if effect_type.startswith("equip_") and effect_type != "equip_device":
            groups[(effect_type, str(row.get("rarity") or ""))].append((key, row["effect_payload"]))

    duplicate_pairs = []
    orange_overlap_pairs = []
    for (slot, rarity), entries in groups.items():
        for i in range(len(entries)):
            k1, p1 = entries[i]
            v1 = [int(p1.get(s, 0)) for s in non_hp_stats]
            n1 = math.sqrt(sum(x * x for x in v1))
            for j in range(i + 1, len(entries)):
                k2, p2 = entries[j]
                v2 = [int(p2.get(s, 0)) for s in non_hp_stats]
                n2 = math.sqrt(sum(x * x for x in v2))
                sim = sum(a * b for a, b in zip(v1, v2)) / (n1 * n2) if (n1 and n2) else 0.0
                if sim >= 0.985:
                    duplicate_pairs.append((round(sim, 4), slot, rarity, k1, k2))
                if rarity == "orange" and sim >= 0.960:
                    orange_overlap_pairs.append((round(sim, 4), slot, k1, k2))

    assert duplicate_pairs == []
    assert orange_overlap_pairs == []


def test_equipment_luck_rarity_ceilings_are_monotonic():
    """各装备槽位的高阶运势上限不应低于低阶，避免蓝/紫压橙倒挂。"""
    from tests.test_item_template_equipment_balance import RARITY_ORDER, _load_item_templates

    items = _load_item_templates()
    slot_rarity_max: dict[str, dict[str, int]] = {}
    for row in items.values():
        effect_type = str(row.get("effect_type") or "")
        if not effect_type.startswith("equip_"):
            continue
        slot = effect_type.removeprefix("equip_")
        rarity = str(row.get("rarity") or "")
        luck = int((row.get("effect_payload") or {}).get("luck", 0) or 0)
        slot_rarity_max.setdefault(slot, {})
        slot_rarity_max[slot][rarity] = max(slot_rarity_max[slot].get(rarity, 0), luck)

    inversions = []
    for slot, rarity_map in slot_rarity_max.items():
        prev_rarity = None
        prev_max = -1
        for rarity in RARITY_ORDER:
            if rarity not in rarity_map:
                continue
            current_max = rarity_map[rarity]
            if prev_rarity is not None and current_max < prev_max:
                inversions.append((slot, prev_rarity, prev_max, rarity, current_max))
            prev_rarity = rarity
            prev_max = current_max

    assert inversions == []


@pytest.mark.django_db
def test_luck_progression_migration_syncs_worn_affixes_and_preserves_naked_stats(django_user_model):
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate
    from guests.models import GearItem, GearTemplate
    from guests.services.equipment_stats import apply_template_stats_to_guest
    from tests.test_equipment_template_sync import _create_guest

    luck_278 = importlib.import_module("gameplay.migrations.0278_equipment_luck_progression")
    manor, guest = _create_guest(django_user_model, "luck_progression_278")
    naked_luck = guest.luck
    naked_force = guest.force

    base_item = ItemTemplate.objects.create(
        key="equip_zhoutianxingpan",
        name="周天星盘",
        effect_type="equip_ornament",
        rarity="orange",
        effect_payload={"hp": 200, "defense": 26, "agility": 14, "luck": 24, "required_level": 75},
    )
    affix_key = luck_278._affix_key("equip_diwangkui", "force", 50)
    affix_item = ItemTemplate.objects.create(
        key=affix_key,
        name="力量之帝王盔",
        effect_type="equip_helmet",
        rarity="orange",
        effect_payload={
            "hp": 420,
            "force": 53,
            "defense": 29,
            "agility": 8,
            "troop_capacity": 20,
            "required_level": 80,
            "equipment_base_key": "equip_diwangkui",
            "equipment_base_name": "帝王盔",
            "equipment_affix": "force",
        },
    )
    down_item = ItemTemplate.objects.create(
        key="equip_guanxingdeng",
        name="观星灯",
        effect_type="equip_ornament",
        rarity="blue",
        effect_payload={"hp": 120, "intellect": 14, "luck": 24, "required_level": 35},
    )

    for item, slot in (
        (base_item, "ornament"),
        (affix_item, "helmet"),
        (down_item, "ornament"),
    ):
        extra = {
            k: v
            for k, v in item.effect_payload.items()
            if k in {"hp", "force", "intellect", "defense", "agility", "luck", "troop_capacity"}
        }
        gear_tpl = GearTemplate.objects.create(
            key=item.key,
            name=item.name,
            slot=slot,
            rarity=item.rarity,
            extra_stats=extra,
        )
        GearItem.objects.create(manor=manor, guest=guest, template=gear_tpl)
        updates: set[str] = set()
        apply_template_stats_to_guest(guest, gear_tpl, 1, updates)
        guest.save(update_fields=list(updates))

    # 执行迁移并验证幂等性
    luck_278.sync_equipment_luck_progression(apps, SimpleNamespace(connection=connection))
    luck_278.sync_equipment_luck_progression(apps, SimpleNamespace(connection=connection))

    guest.refresh_from_db()
    base_item.refresh_from_db()
    affix_item.refresh_from_db()
    down_item.refresh_from_db()

    assert base_item.effect_payload["luck"] == 32
    assert affix_item.effect_payload["luck"] == 10
    assert down_item.effect_payload["luck"] == 18
    assert guest.luck == naked_luck + 32 + 10 + 18
    assert guest.force == naked_force + 53


@pytest.mark.django_db
def test_0279_callback_rebalance_syncs_worn_affixes_and_keeps_strict_rarity_bounds(django_user_model):
    from django.apps import apps
    from django.db import connection

    from core.config import GUEST
    from core.game_data.equipment_value import item_power
    from gameplay.models import ItemTemplate
    from guests.models import GearItem, GearTemplate
    from guests.services.equipment_stats import apply_template_stats_to_guest
    from tests.test_equipment_template_sync import _create_guest
    from tests.test_item_template_equipment_balance import _load_item_templates

    callback_279 = importlib.import_module("gameplay.migrations.0279_equipment_callback_rebalance")
    cal_275 = importlib.import_module("gameplay.migrations.0275_equipment_balance_calibration")

    manor, guest = _create_guest(django_user_model, "callback_279_user")
    guest.level = 80
    guest.current_hp = 99999
    guest.save(update_fields=["level", "current_hp"])
    naked = {field: getattr(guest, field) for field in set(callback_279.STAT_FIELDS.values())}

    old_xiuhuazhen = {"hp": 40, "force": 24, "agility": 15, "luck": 6, "required_level": 55}
    old_lightning = {"hp": 260, "defense": 10, "force": 24, "agility": 32, "required_level": 80}

    xiu_base, _ = ItemTemplate.objects.update_or_create(
        key="equip_xiuhuazhen",
        defaults={
            "name": "绣花针",
            "effect_type": "equip_weapon",
            "rarity": "purple",
            "effect_payload": old_xiuhuazhen,
        },
    )
    xiu_affix_key = cal_275._affix_key("equip_xiuhuazhen", "force", 50)
    xiu_affix_old = cal_275._affix_payload(xiu_base, "force")
    ItemTemplate.objects.update_or_create(
        key=xiu_affix_key,
        defaults={
            "name": "力量之绣花针",
            "effect_type": "equip_weapon",
            "rarity": "purple",
            "effect_payload": xiu_affix_old,
        },
    )
    xiu_affix_tpl, _ = GearTemplate.objects.update_or_create(
        key=xiu_affix_key,
        defaults={
            "name": "力量之绣花针",
            "slot": "weapon",
            "rarity": "purple",
            "base_key": "equip_xiuhuazhen",
            "base_name": "绣花针",
            "extra_stats": {s: xiu_affix_old[s] for s in callback_279.STAT_FIELDS if s in xiu_affix_old},
        },
    )
    ItemTemplate.objects.update_or_create(
        key="equip_shandianqiulongju",
        defaults={
            "name": "闪电虬龙驹",
            "effect_type": "equip_mount",
            "rarity": "orange",
            "effect_payload": old_lightning,
        },
    )
    lightning_tpl, _ = GearTemplate.objects.update_or_create(
        key="equip_shandianqiulongju",
        defaults={
            "name": "闪电虬龙驹",
            "slot": "mount",
            "rarity": "orange",
            "base_key": "equip_shandianqiulongju",
            "base_name": "闪电虬龙驹",
            "extra_stats": {s: old_lightning[s] for s in callback_279.STAT_FIELDS if s in old_lightning},
        },
    )
    for tpl in (xiu_affix_tpl, lightning_tpl):
        GearItem.objects.create(manor=manor, guest=guest, template=tpl)
        updates: set[str] = set()
        apply_template_stats_to_guest(guest, tpl, 1, updates)
        guest.save(update_fields=list(updates))

    callback_279.apply_equipment_callback_rebalance(apps, SimpleNamespace(connection=connection))
    callback_279.apply_equipment_callback_rebalance(apps, SimpleNamespace(connection=connection))

    guest.refresh_from_db()
    xiu_affix_tpl.refresh_from_db()
    lightning_tpl.refresh_from_db()
    assert (
        ItemTemplate.objects.get(key="equip_xiuhuazhen").effect_payload
        == callback_279.EQUIPMENT_PAYLOADS["equip_xiuhuazhen"]
    )
    assert lightning_tpl.extra_stats == {
        s: int(callback_279.EQUIPMENT_PAYLOADS["equip_shandianqiulongju"][s])
        for s in callback_279.STAT_FIELDS
        if s in callback_279.EQUIPMENT_PAYLOADS["equip_shandianqiulongju"]
    }
    for field, bare in naked.items():
        expected = bare
        for tpl in (xiu_affix_tpl, lightning_tpl):
            for stat, target in callback_279.STAT_FIELDS.items():
                if target == field:
                    expected += tpl.extra_stats.get(stat, 0)
        assert getattr(guest, field) == expected, field
    assert guest.max_hp >= int(GUEST.MIN_HP_FLOOR)

    # 校验常规 6 大槽位均不存在“蓝装压紫装”或“非特殊紫装压橙装”的倒挂。
    catalog = _load_item_templates()

    def best_pw(payload):
        return max(item_power(payload, archetype=a, stage="late") for a in ("military", "civil", "neutral"))

    for slot in ("helmet", "armor", "shoes", "weapon", "mount", "ornament"):
        blues = [
            best_pw(r["effect_payload"])
            for r in catalog.values()
            if r["effect_type"] == f"equip_{slot}" and r["rarity"] == "blue"
        ]
        purples = [
            best_pw(r["effect_payload"])
            for r in catalog.values()
            if r["effect_type"] == f"equip_{slot}" and r["rarity"] == "purple"
        ]
        oranges = [
            best_pw(r["effect_payload"])
            for r in catalog.values()
            if r["effect_type"] == f"equip_{slot}" and r["rarity"] == "orange"
        ]
        assert min(purples) > max(blues), slot
        assert min(oranges) >= max(purples), slot
