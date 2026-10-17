"""修行任务的配置、掉落及次数限制回归。"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.mission_reward_content_helpers import assert_fixed_reward, assert_random_group, reward_item_keys

rules = import_module("gameplay.migrations.0204_cultivation_missions")
book_rules = import_module("gameplay.migrations.0205_cultivation_skill_book_tiers")
enemy_rules = import_module("gameplay.migrations.0207_cultivation_enemy_profiles")
alignment_rules = import_module("gameplay.migrations.0208_align_cultivation_difficulty")
DATA = Path(__file__).resolve().parents[1] / "data"
ITEMS = {row["key"]: row for row in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}
MISSIONS = {row["key"]: row for row in yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]}
TIERS = [("yaolu_hujing", 2, 1, 0.01, 0.005), ("xisui_yougu", 2, 2, 0.03, 0.01), ("yijin_mijing", 1, 4, 0.05, 0.02)]


@pytest.mark.parametrize("key,limit,books,wash,fruit", TIERS)
def test_cultivation_rewards_and_frozen_migration_match(key, limit, books, wash, fruit):
    mission = MISSIONS[key]
    expected_mission = deepcopy(rules.MISSION_DEFAULTS[key])
    expected_mission.update(enemy_rules.MISSION_UPDATES[key])
    expected_mission.update(alignment_rules.MISSION_COMBAT_UPDATES[key])
    assert {k: v for k, v in mission.items() if k != "key"} == expected_mission
    assert mission["daily_limit"] == limit
    assert mission["mission_card_daily_limit"] == 0
    assert mission["base_travel_time"] == 1800
    assert not mission["entry_cost"] and not mission["guest_only"]
    assert not mission["available_weekdays"]
    chest_key = "mission_chest_v3_" + key
    assert mission["drop_table"] == {chest_key: 1}
    chest = ITEMS[chest_key]
    defaults = {k: v for k, v in chest.items() if k != "key"}
    defaults["image"] = "items/" + defaults["image"]
    expected = deepcopy(rules.CHEST_DEFAULTS[chest_key])
    expected["description"] = book_rules.CHEST_BOOK_REWARDS[chest_key]["description"]
    expected["effect_payload"] = book_rules.update_book_rewards(chest_key, expected["effect_payload"])
    assert defaults == expected
    assert not chest["tradeable"] and not chest["default_bound"]
    payload = chest["effect_payload"]
    assert_fixed_reward(payload, "yijinjing", books)
    assert_random_group(payload, wash, 1, {"xisuidan": 1})
    assert_random_group(payload, fruit, 1, {"millennium_zhuguo": 1})
    assert not reward_item_keys(payload) & {"mission_card", "recruitment_card"}
    for material in ("yijinjing", "millennium_zhuguo"):
        assert ITEMS[material]["tradeable"] and not ITEMS[material]["default_bound"]
    rarity = "green" if key == "yaolu_hujing" else "blue"
    pool = [
        row
        for row in ITEMS["guild_skill_book_box_" + rarity]["effect_payload"]["skill_book_choices"]
        if ITEMS[row["item_key"]]["rarity"] == rarity
    ]
    assert_random_group(payload, 0.05, 1, {row["item_key"]: row["weight"] for row in pool})
    assert len(pool) == (7 if rarity == "green" else 6)
    assert all(ITEMS[row["item_key"]]["rarity"] == rarity for row in pool)
    assert len(payload["random_item_groups"]) == 3


@pytest.mark.django_db
def test_migration_is_idempotent_and_does_not_modify_other_missions():
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate, MissionTemplate

    unrelated = MissionTemplate.objects.create(key="unrelated_cultivation", name="保留任务", daily_limit=7)
    schema = SimpleNamespace(connection=connection)
    for _ in range(2):
        rules.create_cultivation_missions(apps, schema)
    assert MissionTemplate.objects.filter(key__in=rules.MISSION_DEFAULTS).count() == 3
    assert ItemTemplate.objects.filter(key__in=rules.CHEST_DEFAULTS).count() == 3
    unrelated.refresh_from_db()
    assert unrelated.daily_limit == 7


@pytest.mark.django_db
def test_book_migration_preserves_other_rewards_and_is_idempotent():
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate

    schema = SimpleNamespace(connection=connection)
    rules.create_cultivation_missions(apps, schema)
    unrelated, _ = ItemTemplate.objects.update_or_create(
        key="unrelated_cultivation_box", defaults={"effect_payload": {"keep": 123}}
    )
    for _ in range(2):
        book_rules.sync_book_rewards(apps, schema)
    for key, config in book_rules.CHEST_BOOK_REWARDS.items():
        item = ItemTemplate.objects.get(key=key)
        assert item.description == config["description"]
        original = rules.CHEST_DEFAULTS[key]["effect_payload"]
        assert item.effect_payload == book_rules.update_book_rewards(key, original)
        assert item.effect_payload["item_rewards"] == original["item_rewards"]
        assert item.effect_payload["random_item_groups"][:2] == original["random_item_groups"][:2]
    unrelated.refresh_from_db()
    assert unrelated.effect_payload == {"keep": 123}


@pytest.mark.django_db
@pytest.mark.parametrize("roll", [0.0, 0.999])
@pytest.mark.parametrize("key,limit,books,wash,fruit", TIERS)
def test_real_mission_chests_grant_independent_rewards_and_unbound_materials(
    django_user_model, monkeypatch, roll, key, limit, books, wash, fruit
):
    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.inventory import use as inventory_use
    from gameplay.services.manor.core import ensure_manor
    from gameplay.services.missions_impl.drops import award_mission_drops

    manor = ensure_manor(django_user_model.objects.create_user(username="cultivation_rewards"))
    chest_key = "mission_chest_v3_" + key
    payload = ITEMS[chest_key]["effect_payload"]
    for reward_key in reward_item_keys(payload) | {chest_key}:
        entry = ITEMS[reward_key]
        defaults = {
            field: entry[field]
            for field in ("name", "effect_type", "is_usable", "rarity", "default_bound", "effect_payload")
            if field in entry
        }
        ItemTemplate.objects.update_or_create(key=reward_key, defaults=deepcopy(defaults))
    award_mission_drops(manor, MISSIONS[key]["drop_table"], "修行任务胜利")
    chest = InventoryItem.objects.get(manor=manor, template__key=chest_key)
    assert chest.quantity == 1 and chest.bound_quantity == 0
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: roll)
    monkeypatch.setattr(inventory_use, "weighted_random_choice", lambda keys, weights, rng: keys[0])
    inventory_use.use_inventory_item(chest, manor)
    assert not InventoryItem.objects.filter(pk=chest.pk).exists()
    granted = {row.template.key: row for row in InventoryItem.objects.filter(manor=manor).select_related("template")}
    assert granted["yijinjing"].quantity == books
    assert granted["yijinjing"].bound_quantity == 0
    for material in ("xisuidan", "millennium_zhuguo"):
        assert (material in granted) == (roll == 0)
        if roll == 0:
            assert granted[material].quantity == 1 and granted[material].bound_quantity == 0
    book_keys = {k for k in granted if k.startswith("book_")}
    assert len(book_keys) == int(roll == 0)
    for book_key in book_keys:
        assert granted[book_key].template.rarity == ("green" if key == "yaolu_hujing" else "blue")


@pytest.mark.django_db
@pytest.mark.parametrize("key,limit,books,wash,fruit", TIERS)
def test_task_cards_cannot_bypass_cultivation_daily_limits(django_user_model, key, limit, books, wash, fruit):
    from core.exceptions import MissionDailyLimitError
    from gameplay.models import MissionTemplate
    from gameplay.services.manor.core import ensure_manor
    from gameplay.services.missions_impl.attempts import add_mission_extra_attempt, get_mission_daily_limit

    manor = ensure_manor(django_user_model.objects.create_user(username="cultivation_attempts"))
    mission, _ = MissionTemplate.objects.update_or_create(key=key, defaults=rules.MISSION_DEFAULTS[key])
    assert get_mission_daily_limit(manor, mission) == limit
    with pytest.raises(MissionDailyLimitError, match="不可使用任务卡"):
        add_mission_extra_attempt(manor, mission)
    assert not manor.mission_extra_attempts.exists()


def test_enemy_templates_skills_and_troops_exist():
    guests = {}
    for path in (DATA / "guests").glob("*.yaml"):
        for rows in yaml.safe_load(path.read_text()).get("heroes", {}).values():
            guests.update({row["key"]: row for row in rows})
    skills = {row["key"] for row in yaml.safe_load((DATA / "guest_skills.yaml").read_text())["skills"]}
    troop_data = yaml.safe_load((DATA / "troop_templates.yaml").read_text())
    troops = {row["key"] for row in troop_data["troops"]}
    for key, *_ in TIERS:
        mission = MISSIONS[key]
        assert set(mission["enemy_troops"]) <= troops
        for config in mission["enemy_guests"]:
            assert not guests[config["key"]]["recruitable"]
            assert set(config["skills"]) <= skills


@pytest.mark.django_db
def test_enemy_migration_creates_independent_profiles_and_real_avatar_files(settings, tmp_path):
    from django.apps import apps
    from django.core.files.storage import default_storage
    from django.db import connection

    from gameplay.models import ItemTemplate, MissionTemplate
    from guests.models import GuestTemplate

    settings.MEDIA_ROOT = str(tmp_path)
    schema = SimpleNamespace(connection=connection)
    rules.create_cultivation_missions(apps, schema)
    original = GuestTemplate.objects.create(key="original_guard_test", name="原守卫", base_hp=150000)
    for _ in range(2):
        enemy_rules.sync_cultivation_enemies(apps, schema)
    assert GuestTemplate.objects.filter(key__in=enemy_rules.ENEMY_DEFAULTS).count() == 7
    for key, defaults in enemy_rules.ENEMY_DEFAULTS.items():
        template = GuestTemplate.objects.get(key=key)
        for field, value in defaults.items():
            actual = getattr(template, field)
            assert (str(actual) if field == "avatar" else actual) == value
        assert not template.recruitable
        assert default_storage.exists(str(template.avatar))
        source = DATA / "images" / "guests" / enemy_rules.AVATAR_SOURCES[key]
        with default_storage.open(str(template.avatar), "rb") as stored:
            assert stored.read() == source.read_bytes()
    for key, updates in enemy_rules.MISSION_UPDATES.items():
        mission = MissionTemplate.objects.get(key=key)
        assert mission.enemy_guests == updates["enemy_guests"]
        assert mission.daily_limit == rules.MISSION_DEFAULTS[key]["daily_limit"]
    original.refresh_from_db()
    assert original.base_hp == 150000
    image_rules = import_module("gameplay.migrations.0206_fix_cultivation_chest_images")
    image_rules.fix_chest_images(apps, schema)
    assert set(ItemTemplate.objects.filter(key__in=image_rules.CHEST_KEYS).values_list("image", flat=True)) == {
        image_rules.CHEST_IMAGE
    }


def test_enemy_yaml_reimport_preserves_independent_profiles():
    from guests.management.commands.load_guest_templates import Command

    command = Command()
    payload = yaml.safe_load((DATA / "guests/cultivation.yaml").read_text())
    for rarity, rows in payload["heroes"].items():
        for row in rows:
            built = command._build_template_from_stats(row, rarity, row["archetype"], row["custom_stats"])
            defaults = command._template_defaults(built)
            expected = deepcopy(enemy_rules.ENEMY_DEFAULTS[row["key"]])
            expected.update(alignment_rules.ENEMY_STAT_UPDATES[row["key"]])
            for field, value in expected.items():
                if field != "avatar":
                    assert defaults[field] == value


@pytest.mark.django_db
def test_difficulty_alignment_preserves_rewards_avatars_and_other_missions():
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate, MissionTemplate
    from guests.models import GuestTemplate

    schema = SimpleNamespace(connection=connection)
    rules.create_cultivation_missions(apps, schema)
    book_rules.sync_book_rewards(apps, schema)
    enemy_rules.sync_cultivation_enemies(apps, schema)
    chest_payloads = {
        item.key: deepcopy(item.effect_payload) for item in ItemTemplate.objects.filter(key__in=rules.CHEST_DEFAULTS)
    }
    unchanged_fields = ("drop_table", "daily_limit", "mission_card_daily_limit", "base_travel_time", "is_defense")
    mission_before = {
        mission.key: {field: deepcopy(getattr(mission, field)) for field in unchanged_fields}
        for mission in MissionTemplate.objects.filter(key__in=rules.MISSION_DEFAULTS)
    }
    avatars = dict(GuestTemplate.objects.filter(key__in=enemy_rules.ENEMY_DEFAULTS).values_list("key", "avatar"))
    unrelated = MissionTemplate.objects.create(key="unrelated_alignment", name="保留关卡", enemy_troops={"archer": 123})
    for _ in range(2):
        alignment_rules.align_cultivation_difficulty(apps, schema)
    for key, changes in alignment_rules.MISSION_COMBAT_UPDATES.items():
        mission = MissionTemplate.objects.get(key=key)
        for field, value in changes.items():
            assert getattr(mission, field) == value
        for field, value in mission_before[key].items():
            assert getattr(mission, field) == value
    for key, changes in alignment_rules.ENEMY_STAT_UPDATES.items():
        template = GuestTemplate.objects.get(key=key)
        for field, value in changes.items():
            assert getattr(template, field) == value
        assert str(template.avatar) == avatars[key]
        assert not template.recruitable
    for key, payload in chest_payloads.items():
        assert ItemTemplate.objects.get(key=key).effect_payload == payload
    unrelated.refresh_from_db()
    assert unrelated.enemy_troops == {"archer": 123}
