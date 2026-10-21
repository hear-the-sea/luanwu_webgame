from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.core.files.storage import FileSystemStorage
from django.db import connection
from PIL import Image

from core.exceptions import GuestAlreadyOwnedError, GuestCapacityFullError, InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate, Manor, MissionTemplate
from gameplay.services.inventory.random_source import inventory_random
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, Skill

rules = import_module("gameplay.migrations.0237_surname_li_mission")
name_rules = import_module("gameplay.migrations.0238_surname_li_simplified_name")
balance_rules = import_module("gameplay.migrations.0241_current_mission_balance")
DATA = Path(__file__).resolve().parents[1] / "data"
EXCHANGES = [("li_clan_card", 50, "hero_li_lizhen"), ("villain_card", 300, "hero_heishan_laoyao")]


def _yaml(relative_path):
    return yaml.safe_load((DATA / relative_path).read_text())


def test_surname_li_content_matches_snapshot_and_current_balance_overrides():
    missions = {row["key"]: row for row in _yaml("mission_templates.yaml")["missions"]}
    items = {row["key"]: row for row in _yaml("item_templates.yaml")["items"]}
    skills = {row["key"]: row for row in _yaml("guest_skills.yaml")["skills"]}
    for key, snapshot in rules.MISSION_DEFAULTS.items():
        expected = {
            **snapshot,
            "name": "姓氏传说之“李”",
            "display_order": 1000,
            "enemy_troops": {"dao_sheng": 800, "qiang_wang": 400},
            "enemy_technology": {"level": 10, "guest_level": 100, "guest_bonus": 0.25},
        }
        assert {field: missions[key][field] for field in snapshot} == expected
    for key, snapshot in rules.ITEM_DEFAULTS.items():
        current = deepcopy(items[key])
        current["image"] = "items/" + current["image"]
        assert {field: current[field] for field in snapshot} == snapshot
    for key, snapshot in rules.SKILL_DEFAULTS.items():
        assert {field: skills[key][field] for field in snapshot} == snapshot
    for rarity, entries in _yaml("guests/surname_li.yaml")["heroes"].items():
        for entry in entries:
            overrides = {
                "hero_li_lizhen": {
                    "growth_range": [6, 12],
                    "attribute_weights": {"force": 10, "intellect": 45, "defense": 20, "agility": 25},
                },
                "hero_heishan_laoyao": {"growth_range": [6, 12]},
                "task_surname_li_elai": {"base_hp": 300000},
            }
            snapshot = {**rules.GUEST_DEFAULTS[entry["key"]], **overrides.get(entry["key"], {})}
            for field in ("name", "archetype", "recruitable", "default_gender", "flavor"):
                assert snapshot[field] == entry[field]
            assert snapshot["rarity"] == rarity
            assert snapshot["avatar"] == "guests/" + entry["avatar"]
            assert snapshot["growth_range"] == entry.get("growth_range", [])
            assert snapshot["attribute_weights"] == entry.get("attribute_weights", {})
            assert snapshot["default_morality"] == entry.get("default_morality", 70)
            assert not snapshot["is_hermit"] and not snapshot["is_world_unique"]
            assert rules.GUEST_SKILLS[entry["key"]] == entry["skills"]
            for field, value in entry["custom_stats"].items():
                target = "base_attack" if field == "force" else field if field == "base_hp" else "base_" + field
                assert snapshot[target] == value


@pytest.fixture
def surname_li_content(db, tmp_path, monkeypatch):
    storage = FileSystemStorage(location=tmp_path / "media")
    monkeypatch.setattr(rules, "default_storage", storage)
    rules.sync_surname_li_mission(apps, SimpleNamespace(connection=connection))
    return storage


@pytest.mark.django_db
def test_surname_li_sync_is_idempotent_and_preserves_unrelated_content(surname_li_content):
    mission = MissionTemplate.objects.create(key="surname_unrelated", name="其他任务", daily_limit=17)
    skill, _ = Skill.objects.update_or_create(key="stratagem_burst", defaults={"name": "已定制技能", "base_power": 987})
    counts = (
        MissionTemplate.objects.count(),
        ItemTemplate.objects.count(),
        GuestTemplate.objects.count(),
        Skill.objects.count(),
    )
    for _ in range(2):
        rules.sync_surname_li_mission(apps, SimpleNamespace(connection=connection))
    assert counts == (
        MissionTemplate.objects.count(),
        ItemTemplate.objects.count(),
        GuestTemplate.objects.count(),
        Skill.objects.count(),
    )
    mission.refresh_from_db()
    skill.refresh_from_db()
    assert (mission.name, mission.daily_limit) == ("其他任务", 17)
    assert (skill.name, skill.base_power) == ("已定制技能", 987)
    for key, keys in rules.GUEST_SKILLS.items():
        assert set(GuestTemplate.objects.get(key=key).initial_skills.values_list("key", flat=True)) == set(keys)


@pytest.mark.django_db
def test_simplified_name_migration_only_renames_surname_mission(surname_li_content):
    mission = MissionTemplate.objects.get(key="surname_legend_li")
    original_technology = deepcopy(mission.enemy_technology)
    unrelated = MissionTemplate.objects.create(key="unrelated_surname_name", name="其他任务")
    for _ in range(2):
        name_rules.simplify_mission_name(apps, SimpleNamespace(connection=connection))
    mission.refresh_from_db()
    unrelated.refresh_from_db()
    assert mission.name == "姓氏传说之“李”"
    assert mission.enemy_technology == original_technology
    assert unrelated.name == "其他任务"


@pytest.mark.django_db
def test_forward_balance_migrations_match_yaml_and_preserve_player_data(surname_li_content, manor_with_user):
    manor, _ = manor_with_user
    guest = Guest.objects.create(
        manor=manor,
        template=GuestTemplate.objects.get(key="hero_li_lizhen"),
        level=31,
        current_hp=123,
        force=237,
        intellect=345,
    )
    cards = _cards(manor, "li_clan_card", 17)
    guest_before = Guest.objects.filter(pk=guest.pk).values().get()
    cards_before = InventoryItem.objects.filter(pk=cards.pk).values().get()
    templates_before = {
        template.key: (
            str(template.avatar),
            template.flavor,
            list(template.initial_skills.values_list("key", flat=True)),
        )
        for template in GuestTemplate.objects.filter(key__in=rules.GUEST_DEFAULTS)
    }
    cellar, _ = MissionTemplate.objects.update_or_create(
        key="tianpeng_dijiao_2",
        defaults={
            "name": "地窖第二层",
            "enemy_technology": {"level": 9, "guest_level": 80, "guest_bonus": 0.2, "levels": {"wolf_hp": 3}},
        },
    )
    unrelated = MissionTemplate.objects.create(
        key="unrelated_balance_mission", name="其他任务", enemy_technology={"guest_level": 17}
    )
    schema_editor = SimpleNamespace(connection=connection)
    for module_name, function_name in [
        ("0238_surname_li_simplified_name", "simplify_mission_name"),
        ("0239_clear_regular_mission_order", "clear_regular_mission_order"),
        ("0240_zhanlong_progressive_difficulty", "sync_zhanlong_progression"),
    ]:
        getattr(import_module(f"gameplay.migrations.{module_name}"), function_name)(apps, schema_editor)
    for _ in range(2):
        balance_rules.sync_current_mission_balance(apps, schema_editor)

    mission = MissionTemplate.objects.get(key="surname_legend_li")
    mission_yaml = next(row for row in _yaml("mission_templates.yaml")["missions"] if row["key"] == mission.key)
    for field in rules.MISSION_DEFAULTS[mission.key]:
        assert getattr(mission, field) == mission_yaml[field]
    for entries in _yaml("guests/surname_li.yaml")["heroes"].values():
        for entry in entries:
            template = GuestTemplate.objects.get(key=entry["key"])
            for field in balance_rules.GUEST_UPDATES[entry["key"]]:
                expected = entry["custom_stats"][field] if field == "base_hp" else entry[field]
                assert getattr(template, field) == expected
            assert (
                str(template.avatar),
                template.flavor,
                list(template.initial_skills.values_list("key", flat=True)),
            ) == templates_before[template.key]
    cellar.refresh_from_db()
    assert cellar.enemy_technology == {
        "level": 9,
        "guest_level": 100,
        "guest_bonus": 0.2,
        "levels": {"wolf_hp": 3},
    }
    unrelated.refresh_from_db()
    assert unrelated.enemy_technology == {"guest_level": 17}
    assert Guest.objects.filter(pk=guest.pk).values().get() == guest_before
    assert InventoryItem.objects.filter(pk=cards.pk).values().get() == cards_before


@pytest.mark.django_db
def test_forward_balance_migration_preserves_other_json_settings(surname_li_content):
    mission = MissionTemplate.objects.get(key="surname_legend_li")
    mission.enemy_troops["arrow_god"] = 3
    mission.enemy_technology["levels"] = {"wolf_hp": 2}
    mission.save(update_fields=["enemy_troops", "enemy_technology"])
    balance_rules.sync_current_mission_balance(apps, SimpleNamespace(connection=connection))
    mission.refresh_from_db()
    assert mission.enemy_troops == {"dao_sheng": 800, "qiang_wang": 400, "arrow_god": 3}
    assert mission.enemy_technology == {
        "level": 10,
        "guest_level": 100,
        "guest_bonus": 0.25,
        "levels": {"wolf_hp": 2},
    }


@pytest.mark.django_db
def test_surname_li_migration_stores_seven_valid_webp_images(surname_li_content):
    assert len(rules.IMAGE_SOURCES) == 7
    referenced = {str(row.image) for row in ItemTemplate.objects.filter(key__in=rules.ITEM_DEFAULTS)}
    referenced.update(str(row.avatar) for row in GuestTemplate.objects.filter(key__in=rules.GUEST_DEFAULTS))
    assert referenced == set(rules.IMAGE_SOURCES)
    for target in referenced:
        assert target.endswith(".webp")
        with surname_li_content.open(target, "rb") as stream, Image.open(stream) as image:
            assert image.format == "WEBP"
            image.verify()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "li_roll,villain_roll,expected_li,expected_villain",
    [(0.099, 0.499, 1, 1), (0.1, 0.499, 0, 1), (0.099, 0.5, 1, 0), (0.1, 0.5, 0, 0)],
)
def test_li_chest_real_use_has_fixed_plums_and_independent_card_boundaries(
    surname_li_content, manor_with_user, monkeypatch, li_roll, villain_roll, expected_li, expected_villain
):
    manor, _ = manor_with_user
    box = InventoryItem.objects.create(
        manor=manor,
        template=ItemTemplate.objects.get(key="mission_chest_v3_surname_legend_li"),
        quantity=1,
        bound_quantity=1,
    )
    rolls = iter([li_roll, villain_roll])
    monkeypatch.setattr(inventory_random, "random", lambda: next(rolls))
    monkeypatch.setattr(inventory_random, "uniform", lambda low, high: low)
    use_inventory_item(box, manor)
    assert not InventoryItem.objects.filter(pk=box.pk).exists()
    for key, expected in [("li_plum", 2), ("li_clan_card", expected_li), ("villain_card", expected_villain)]:
        stock = InventoryItem.objects.filter(manor=manor, template__key=key).first()
        assert (stock.quantity if stock else 0) == expected
        assert (stock.bound_quantity if stock else 0) == expected
    assert next(rolls, None) is None


def _cards(manor, key, quantity):
    return InventoryItem.objects.create(
        manor=manor, template=ItemTemplate.objects.get(key=key), quantity=quantity, bound_quantity=quantity
    )


@pytest.mark.django_db
@pytest.mark.parametrize("card_key,required,guest_key", EXCHANGES)
def test_surname_cards_exchange_exact_total_for_purple_guest_in_each_manor(
    surname_li_content, django_user_model, card_key, required, guest_key
):
    for index in range(2):
        manor = ensure_manor(django_user_model.objects.create_user(username=f"surname_exchange_{index}"))
        cards = _cards(manor, card_key, required)
        use_inventory_item(cards, manor)
        assert not InventoryItem.objects.filter(pk=cards.pk).exists()
        guest = Guest.objects.get(manor=manor, template__key=guest_key)
        assert guest.template.rarity == "purple"
        assert set(guest.skills.values_list("key", flat=True)) >= set(rules.GUEST_SKILLS[guest_key])


@pytest.mark.django_db
@pytest.mark.parametrize("card_key,required,guest_key", EXCHANGES)
def test_surname_cards_one_short_does_not_consume_or_create_guest(
    surname_li_content, manor_with_user, card_key, required, guest_key
):
    manor, _ = manor_with_user
    cards = _cards(manor, card_key, required - 1)
    with pytest.raises(InsufficientStockError):
        use_inventory_item(cards, manor)
    cards.refresh_from_db()
    assert (cards.quantity, cards.bound_quantity) == (required - 1, required - 1)
    assert not Guest.objects.filter(manor=manor, template__key=guest_key).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("card_key,required,guest_key", EXCHANGES)
def test_surname_cards_already_owned_preserves_all_cards(
    surname_li_content, manor_with_user, card_key, required, guest_key
):
    manor, _ = manor_with_user
    Guest.objects.create(manor=manor, template=GuestTemplate.objects.get(key=guest_key))
    cards = _cards(manor, card_key, required)
    with pytest.raises(GuestAlreadyOwnedError):
        use_inventory_item(cards, manor)
    cards.refresh_from_db()
    assert (cards.quantity, cards.bound_quantity) == (required, required)
    assert Guest.objects.filter(manor=manor, template__key=guest_key).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("card_key,required,guest_key", EXCHANGES)
def test_surname_cards_full_capacity_preserves_inventory_and_guest_count(
    surname_li_content, manor_with_user, monkeypatch, card_key, required, guest_key
):
    manor, _ = manor_with_user
    cards = _cards(manor, card_key, required)
    before_count = manor.guests.count()
    monkeypatch.setattr(Manor, "guest_capacity", property(lambda _manor: 0))
    with pytest.raises(GuestCapacityFullError):
        use_inventory_item(cards, manor)
    cards.refresh_from_db()
    assert (cards.quantity, cards.bound_quantity) == (required, required)
    assert manor.guests.count() == before_count
    assert not Guest.objects.filter(manor=manor, template__key=guest_key).exists()
