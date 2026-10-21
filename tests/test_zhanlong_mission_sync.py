"""战龙配置快照、增量同步与真实开箱链路回归。"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from random import Random
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

rules = import_module("gameplay.migrations.0220_zhanlong_quest_chain")
reward_rules = import_module("gameplay.migrations.0221_chiyou_rewards_and_unique_guests")
equipment_rules = import_module("gameplay.migrations.0222_zhaoyun_equipment_rewards")
profile_rules = import_module("gameplay.migrations.0227_zhanlong_current_combat_profiles")
alignment_rules = import_module("gameplay.migrations.0229_personal_guild_guest_profile_alignment")
armor_luck_rules = import_module("gameplay.migrations.0230_chiyou_armor_luck")
DATA = Path(__file__).resolve().parents[1] / "data"


def _catalog(filename, section):
    return {row["key"]: row for row in yaml.safe_load((DATA / filename).read_text())[section]}


def test_frozen_migration_matches_current_chain_and_skill_configuration():
    for filename, section, snapshots in (
        ("mission_templates.yaml", "missions", rules.MISSION_DEFAULTS),
        ("guest_skills.yaml", "skills", rules.SKILL_DEFAULTS),
    ):
        entries = _catalog(filename, section)
        for key, defaults in snapshots.items():
            final_defaults = {**defaults, **profile_rules.MISSION_UPDATES.get(key, {})}
            assert {field: value for field, value in entries[key].items() if field != "key"} == final_defaults
    items = _catalog("item_templates.yaml", "items")
    for key, defaults in {**rules.ITEM_DEFAULTS, **reward_rules.ITEM_DEFAULTS, **equipment_rules.ITEM_DEFAULTS}.items():
        expected = {field: value for field, value in items[key].items() if field != "key"}
        image_sources = (
            equipment_rules.IMAGE_SOURCES
            if key in equipment_rules.ITEM_DEFAULTS
            else reward_rules.IMAGE_SOURCES if key in reward_rules.ITEM_DEFAULTS else rules.IMAGE_SOURCES
        )
        assert image_sources[defaults["image"]] == "items/" + expected["image"]
        expected["image"] = defaults["image"]
        final_defaults = {**defaults, **alignment_rules.ITEM_STORAGE_OVERRIDES.get(key, {})}
        if key in armor_luck_rules.EQUIPMENT_PAYLOAD_OVERRIDES:
            final_defaults["effect_payload"] = {
                **final_defaults["effect_payload"],
                **armor_luck_rules.EQUIPMENT_PAYLOAD_OVERRIDES[key],
            }
        assert expected == final_defaults
    for source in rules.IMAGE_SOURCES.values():
        assert (DATA / "images" / source).is_file()


@pytest.fixture
def synced_chain(db, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    rules.sync_zhanlong_missions(apps, SimpleNamespace(connection=connection))
    reward_rules.sync_chiyou_rewards(apps, SimpleNamespace(connection=connection))
    equipment_rules.sync_zhaoyun_equipment_rewards(apps, SimpleNamespace(connection=connection))


@pytest.mark.django_db
def test_sync_is_repeatable_preserves_other_content_and_stores_images(
    settings, tmp_path, django_capture_on_commit_callbacks
):
    from django.core.cache import cache
    from django.core.files.storage import default_storage

    from gameplay.models import ItemTemplate, MissionTemplate
    from guests.models import GuestTemplate, Skill

    settings.MEDIA_ROOT = tmp_path
    unrelated = MissionTemplate.objects.create(key="zhanlong_unrelated", name="保留任务", daily_limit=7)
    hero = GuestTemplate.objects.create(key="zhanlong_existing_hero", name="保留英雄", base_attack=123)
    draw, _ = Skill.objects.update_or_create(
        key="draw_enemy_blades", defaults={"name": "已有引敌技能", "passive_config": {"preserve": True}}
    )
    cache.set("battle:guest_templates", [hero.key])
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(2):
            rules.sync_zhanlong_missions(apps, SimpleNamespace(connection=connection))
    assert cache.get("battle:guest_templates") is None
    assert MissionTemplate.objects.filter(key__in=rules.MISSION_DEFAULTS).count() == 4
    assert GuestTemplate.objects.filter(key__in=rules.ENEMY_DEFAULTS).count() == 8
    assert ItemTemplate.objects.filter(key__in=rules.ITEM_DEFAULTS).count() == 7
    for key, defaults in rules.ENEMY_DEFAULTS.items():
        enemy = GuestTemplate.objects.get(key=key)
        assert not enemy.recruitable
        assert enemy.base_attack == defaults["base_attack"]
        assert set(enemy.initial_skills.values_list("key", flat=True)) == set(rules.ENEMY_SKILLS[key])
        assert default_storage.exists(str(enemy.avatar))
    for key in rules.ITEM_DEFAULTS:
        assert default_storage.exists(str(ItemTemplate.objects.get(key=key).image))
    draw.refresh_from_db()
    assert draw.passive_config == {"preserve": True}
    unrelated.refresh_from_db()
    hero.refresh_from_db()
    assert unrelated.daily_limit == 7 and hero.base_attack == 123


@pytest.mark.django_db
@pytest.mark.parametrize("roll", [0.0, 0.999])
def test_real_mission_chests_supply_each_next_entry_and_independent_scroll(
    synced_chain, django_user_model, monkeypatch, roll
):
    from gameplay.models import InventoryItem, ItemTemplate, MissionTemplate
    from gameplay.services.inventory import use as inventory_use
    from gameplay.services.inventory.core import get_item_quantity
    from gameplay.services.manor.core import ensure_manor
    from gameplay.services.missions_impl.drops import award_mission_drops
    from gameplay.views.mission_helpers import build_entry_cost_list
    from tests.mission_reward_content_helpers import reward_item_keys

    manor = ensure_manor(django_user_model.objects.create_user(username="zhanlong_chain"))
    items = _catalog("item_templates.yaml", "items")
    # 同步四箱实际引用的既有奖励，避免依赖测试库预装配置。
    model_fields = {field.name for field in ItemTemplate._meta.fields} - {"id", "key", "image"}
    for defaults in {**rules.ITEM_DEFAULTS, **reward_rules.ITEM_DEFAULTS, **equipment_rules.ITEM_DEFAULTS}.values():
        payload = defaults.get("effect_payload", {})
        reward_keys = reward_item_keys(payload)
        reward_keys.update(
            row["item_key"] for entries in payload.get("blueprint_material_support", {}).values() for row in entries
        )
        for key in reward_keys:
            entry = items[key]
            ItemTemplate.objects.get_or_create(
                key=key,
                defaults={field: deepcopy(value) for field, value in entry.items() if field in model_fields},
            )
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: roll)
    monkeypatch.setattr(inventory_use.inventory_random, "randint", lambda minimum, maximum: minimum)
    monkeypatch.setattr(inventory_use, "weighted_random_choice", lambda keys, weights, rng: keys[0])
    ordered_keys = list(rules.MISSION_DEFAULTS)
    for index, key in enumerate(ordered_keys):
        mission = MissionTemplate.objects.get(key=key)
        assert not mission.mission_card_daily_limit
        award_mission_drops(manor, mission.drop_table, "战龙演武胜利")
        chest = InventoryItem.objects.get(manor=manor, template__key="mission_chest_v3_" + key)
        inventory_use.use_inventory_item(chest, manor)
        assert not InventoryItem.objects.filter(pk=chest.pk).exists()
        if index < len(ordered_keys) - 1:
            next_mission = MissionTemplate.objects.get(key=ordered_keys[index + 1])
            templates = {row.key: row for row in ItemTemplate.objects.filter(key__in=next_mission.entry_cost)}
            _costs, can_afford = build_entry_cost_list(
                next_mission, templates, manor, get_item_quantity_func=get_item_quantity
            )
            assert can_afford
    assert InventoryItem.objects.filter(manor=manor, template__key="zhaoyun_guest_scroll").exists() == (roll == 0)


@pytest.mark.django_db
@pytest.mark.parametrize("mission_key", list(rules.MISSION_DEFAULTS))
def test_synced_bosses_execute_in_formal_engine_with_only_real_troops(synced_chain, mission_key):
    from battle.combatants_pkg import (
        assign_agility_based_priorities,
        build_guest_combatants,
        build_named_ai_guests,
        build_troop_combatants,
        clear_guest_template_cache,
    )
    from battle.simulation.battle_flow import simulate_battle
    from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels

    clear_guest_template_cache()
    mission = rules.MISSION_DEFAULTS[mission_key]
    tech = mission["enemy_technology"]
    enemy_guests = build_named_ai_guests(mission["enemy_guests"], level=tech["guest_level"], rng=Random(13))
    defenders = build_guest_combatants(enemy_guests, "defender", stat_bonuses=get_guest_stat_bonuses(tech))
    for enemy in defenders:
        assert {skill["key"] for skill in enemy.skills} == set(rules.ENEMY_SKILLS[enemy.template_key])
    defenders += build_troop_combatants(
        mission["enemy_troops"], "defender", tech_levels=resolve_enemy_tech_levels(tech)
    )
    assert sum(unit.initial_troop_strength for unit in defenders) == sum(mission["enemy_troops"].values())
    players = build_named_ai_guests(
        rules.MISSION_DEFAULTS["zhanlong_wuhu_shizhen"]["enemy_guests"], level=100, rng=Random(23)
    )
    attackers = build_guest_combatants(players, "attacker", stat_bonuses={"attack": 1, "defense": 1, "hp": 1})
    assign_agility_based_priorities(attackers, defenders)
    result = simulate_battle(attackers, defenders, Random(31), seed=31, travel_seconds=0, config={}, max_rounds=8)
    assert result.rounds
    assert all(unit.hp >= 0 for unit in attackers + defenders)
    if mission_key == "zhanlong_changban_duanqiao":
        dust = [event for row in result.rounds for event in row.get("events", []) if event.get("effect") == "疑兵扬尘"]
        assert len(dust) == 1
