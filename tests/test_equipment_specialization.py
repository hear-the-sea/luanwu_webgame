"""普通装备专精预算、属性语义与历史装备同步回归。"""

import copy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

migration = importlib.import_module("gameplay.migrations.0211_equipment_specialization")
previous = importlib.import_module("gameplay.migrations.0198_equipment_balance_and_requirements")


def _items():
    rows = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())["items"]
    return {row["key"]: row for row in rows}


def test_specialization_conserves_budget_and_preserves_metadata():
    assert len(migration.EQUIPMENT_PAYLOADS) == 45
    exceptions = {"equip_taiqingyuce": 4}
    for key, after in migration.EQUIPMENT_PAYLOADS.items():
        assert not key.startswith("soulorn_")
        if key not in previous.EQUIPMENT_PAYLOADS:
            assert key in {"equip_shandianqiulongju", "equip_jinjingbailongju"}
            continue
        before = previous.EQUIPMENT_PAYLOADS[key]
        assert sum(after.get(s, 0) - before.get(s, 0) for s in migration.AFFIXES) == exceptions.get(key, 0)
        assert after.get("hp", 0) <= before.get("hp", 0)
        assert after.get("luck", 0) == before.get("luck", 0)
        assert after.get("troop_capacity", 0) == before.get("troop_capacity", 0)
        for field in ("set_key", "set_bonus", "required_level", "troop_stat_bonus"):
            assert after.get(field) == before.get(field)


@pytest.mark.parametrize("affix", migration.AFFIXES)
def test_frozen_migration_affixes_match_runtime_for_every_changed_base(affix):
    from gameplay.services.equipment_affixes import build_equipment_affix_payload, equipment_affix_key

    items = _items()
    for key, payload in migration.EQUIPMENT_PAYLOADS.items():
        base = SimpleNamespace(key=key, name=items[key]["name"], effect_payload=payload)
        expected = build_equipment_affix_payload(base, affix)
        if expected is None:
            expected = {
                **payload,
                "equipment_base_key": key,
                "equipment_base_name": base.name,
                "equipment_affix": affix,
            }
        assert migration._affix_payload(base, affix) == expected
        assert migration._affix_key(key, affix, 50) == equipment_affix_key(key, affix)
        assert migration._affix_payload(base, affix).get("luck", 0) == payload.get("luck", 0)
    long_key = "equip_" + "long_name_" * 8
    assert migration._affix_key(long_key, affix, 50) == equipment_affix_key(long_key, affix)


def test_matching_specializations_have_clear_quality_progression():
    items = _items()
    chains = {
        "force": ("equip_lieyandao", "equip_tulongdao", "equip_xuantiechongjian"),
        "intellect": ("equip_xingluoshan", "equip_tianjizhang", "equip_zhuxianjian"),
        "agility": ("equip_lingboxie", "equip_zhurixue", "equip_shenxingxue"),
    }
    # 实际目录键与升级链一致；至少约两成的主属性进阶。
    for stat, keys in chains.items():
        values = [items[key]["effect_payload"][stat] for key in keys]
        assert all(b >= a * 1.2 for a, b in zip(values, values[1:])), (stat, values)
    for a, b in (("equip_lingboxie", "equip_yingxingxue"), ("equip_wuzhuima", "equip_zhaohuangfeidian")):
        assert items[a]["effect_payload"] != items[b]["effect_payload"]


@pytest.mark.django_db
@pytest.mark.parametrize("health", [1, 999999])
def test_migration_preserves_naked_stats_allocations_inventory_sets_and_soul(django_user_model, health):
    from django.apps import apps
    from django.db import connection

    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.equipment_affixes import build_equipment_affix_payload, equipment_affix_key
    from guests.models import GearItem, GearTemplate
    from guests.services.equipment_payloads import build_gear_template_defaults
    from guests.services.equipment_stats import apply_template_stats_to_guest
    from tests.test_equipment_template_sync import _create_guest

    manor, guest = _create_guest(django_user_model, f"specialization_{health}")
    guest.level = 100
    guest.allocated_force = 99
    guest.force += 99
    guest.gear_set_bonus = {"defense": 7}
    guest.defense_stat += 7
    guest.save()
    fields = set(migration.STAT_FIELDS.values()) | {"attack_bonus", "defense_bonus"}
    naked = {field: getattr(guest, field) for field in fields}
    key = "equip_hanxuebaoma"
    base = ItemTemplate.objects.create(
        key=key,
        name="汗血宝马",
        effect_type="equip_mount",
        rarity="orange",
        effect_payload=copy.deepcopy(previous.EQUIPMENT_PAYLOADS[key]),
    )
    affix = "defense"
    variant = ItemTemplate.objects.create(
        key=equipment_affix_key(key, affix),
        name="防御之汗血宝马",
        effect_type="equip_mount",
        rarity="orange",
        effect_payload=build_equipment_affix_payload(base, affix),
    )
    inventory = InventoryItem.objects.create(manor=manor, template=variant, quantity=3, bound_quantity=1)
    worn = GearTemplate.objects.create(key=variant.key, **build_gear_template_defaults(variant, slot="mount"))
    gear = GearItem.objects.create(manor=manor, guest=guest, template=worn)
    updates = set()
    apply_template_stats_to_guest(guest, worn, 1, updates)
    # 仅有 GearTemplate 的历史前缀也必须更新。
    orphan_key = equipment_affix_key(key, "force")
    orphan_item = SimpleNamespace(
        key=orphan_key,
        name="力量之汗血宝马",
        rarity="orange",
        effect_payload=build_equipment_affix_payload(base, "force"),
    )
    orphan = GearTemplate.objects.create(key=orphan_key, **build_gear_template_defaults(orphan_item, slot="mount"))
    soul_payload = {"hp": 1234, "force": 13, "intellect": 34, "defense": 12, "agility": 19, "luck": 42}
    soul = ItemTemplate.objects.create(
        key="soulorn_specialization",
        name="魂饰",
        effect_type="equip_ornament",
        rarity="purple",
        effect_payload=soul_payload,
    )
    soul_gear = GearTemplate.objects.create(key=soul.key, **build_gear_template_defaults(soul, slot="ornament"))
    GearItem.objects.create(manor=manor, guest=guest, template=soul_gear)
    apply_template_stats_to_guest(guest, soul_gear, 1, updates)
    guest.current_hp = health
    guest.save()
    counts = (ItemTemplate.objects.count(), GearTemplate.objects.count(), GearItem.objects.count())
    migration.apply_equipment_specialization(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    base.refresh_from_db()
    variant.refresh_from_db()
    worn.refresh_from_db()
    orphan.refresh_from_db()
    inventory.refresh_from_db()
    soul.refresh_from_db()
    soul_gear.refresh_from_db()
    assert counts == (ItemTemplate.objects.count(), GearTemplate.objects.count(), GearItem.objects.count())
    assert base.effect_payload == migration.EQUIPMENT_PAYLOADS[key]
    assert variant.effect_payload == build_equipment_affix_payload(base, affix)
    assert orphan.extra_stats == {
        s: v for s, v in build_equipment_affix_payload(base, "force").items() if s in migration.STAT_FIELDS
    }
    assert worn.base_key == key
    assert inventory.template_id == variant.pk
    assert (inventory.quantity, inventory.bound_quantity) == (3, 1)
    assert GearItem.objects.get(pk=gear.pk).guest_id == guest.pk
    assert soul.effect_payload == soul_payload
    assert soul_gear.extra_stats == soul_payload
    assert guest.allocated_force == 99 and guest.level == 100
    assert guest.gear_set_bonus == {"defense": 7}
    assert guest.current_hp == min(health, guest.max_hp)
    for stat, field in migration.STAT_FIELDS.items():
        assert getattr(guest, field) == naked[field] + worn.extra_stats.get(stat, 0) + soul_payload.get(stat, 0)
    first = {field: getattr(guest, field) for field in fields | {"current_hp"}}
    migration.apply_equipment_specialization(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    assert first == {field: getattr(guest, field) for field in first}


def test_100_level_builds_stay_bounded_using_real_attack_defense_and_skill_formulas():
    import json

    from battle.simulation.damage_calculation import _calculate_damage_reduction
    from battle.utils.battle_calculator import calculate_skill_bonus
    from guests.guest_rules import build_guest_stat_block

    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "reports/equipment_balance_proposal_20261018.json").read_text())
    skills = yaml.safe_load((root / "data/guest_skills.yaml").read_text())["skills"]
    for example in report["candidate"]["whole_guest_examples"]:
        if example["rarity"] != "purple" or example["level"] != 100:
            continue
        baseline = report["current"]["guest_expected"][f"purple/{example['archetype']}/100"]
        build = next(b for b in report["candidate"]["builds"] if b["name"] == example["build"])
        actors = []
        for phase in ("current", "candidate"):
            equipment = build[phase]
            values = {stat: baseline[stat] + equipment.get(stat, 0) for stat in migration.AFFIXES}
            for stat, amount in example["allocation"].items():
                values[stat] += amount
            guest = SimpleNamespace(
                **values,
                defense_stat=values["defense"],
                archetype=example["archetype"],
                template=SimpleNamespace(base_hp=800),
                hp_bonus=equipment.get("hp", 0),
                attack_bonus=equipment.get("attack", 0),
            )
            block = build_guest_stat_block(guest)
            actors.append(
                SimpleNamespace(
                    kind="guest",
                    force_attr=values["force"],
                    intellect_attr=values["intellect"],
                    defense=block["defense"],
                    attack=block["attack"],
                    agility=values["agility"],
                    luck=baseline["luck"] + equipment.get("luck", 0),
                    hp=block["hp"],
                )
            )
        before, after = actors
        assert after.attack <= before.attack * 1.06
        assert after.luck == before.luck
        old_reduction = _calculate_damage_reduction(before, before, before.defense)
        new_reduction = _calculate_damage_reduction(after, after, after.defense)
        assert after.hp / (1 - new_reduction) <= before.hp / (1 - old_reduction) * 1.03
        # 智力/敏捷/运势还参与技能公式；同时复核双维取最小值的神意破虚。
        target = SimpleNamespace(
            kind="guest", defense=300, intellect_attr=300, force_attr=300, attack=300, agility=200, luck=100, hp=20000
        )
        for skill in skills:
            old_bonus = calculate_skill_bonus(skill, before, target)
            new_bonus = calculate_skill_bonus(skill, after, target)
            if old_bonus > 0:
                assert new_bonus <= old_bonus * 1.10, (example["build"], skill["key"], old_bonus, new_bonus)
