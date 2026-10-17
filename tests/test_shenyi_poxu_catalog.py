import importlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

migration = importlib.import_module("gameplay.migrations.0199_shenyi_poxu_skill_book")
ROOT = Path(__file__).resolve().parents[1]


def test_shenyi_poxu_config_has_balanced_single_target_formula_and_learning_gates():
    skills = yaml.safe_load((ROOT / "data/guest_skills.yaml").read_text())["skills"]
    matches = [row for row in skills if row["key"] == migration.SKILL_KEY]
    assert len(matches) == 1
    row = matches[0]
    assert row["name"] == "神意破虚"
    for field, value in row.items():
        if field != "key":
            assert migration.SKILL_DEFAULTS[field] == value
    assert row["kind"] == "active"
    assert row["rarity"] == "purple"
    assert row["targets"] == 1
    assert row["required_level"] == 80
    assert row["required_force"] == row["required_intellect"] == 400
    assert row["damage_formula"] == {
        "base": 5000,
        "ally_min": {"stats": ["force", "intellect"], "coefficient": 15},
        "enemy": {"defense": 0.3},
    }


def test_shenyi_poxu_book_matches_snapshot_and_has_only_requested_sources():
    rows = yaml.safe_load((ROOT / "data/item_templates.yaml").read_text())["items"]
    matches = [row for row in rows if row["key"] == migration.BOOK_KEY]
    assert len(matches) == 1
    row = matches[0]
    assert row == {"key": migration.BOOK_KEY, "image": "skill3.png", **migration.BOOK_DEFAULTS}
    sources = {
        other["key"]
        for other in rows
        if other["key"] != migration.BOOK_KEY and migration.BOOK_KEY in repr(other.get("effect_payload", {}))
    }
    assert sources == {
        "mission_chest_fengbang_tianmen",
        "mission_chest_v3_fengbang_tianmen",
        "guild_skill_book_box_master",
    }


@pytest.mark.django_db
def test_shenyi_poxu_migration_updates_in_place_and_reuses_existing_media():
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate
    from guests.models import Skill

    ItemTemplate.objects.create(key="book_brahma_true_fire", name="reference", image="items/existing_skill3.webp")
    skill, _ = Skill.objects.update_or_create(key=migration.SKILL_KEY, defaults={"name": "old", "required_force": 1})
    book, _ = ItemTemplate.objects.update_or_create(
        key=migration.BOOK_KEY, defaults={"name": "old", "image": "items/old.webp"}
    )
    unrelated = ItemTemplate.objects.create(key="shenyi_unrelated", name="untouched", effect_payload={"x": 1})
    migration.add_shenyi_poxu(apps, SimpleNamespace(connection=connection))
    migration.add_shenyi_poxu(apps, SimpleNamespace(connection=connection))
    skill.refresh_from_db()
    book.refresh_from_db()
    assert Skill.objects.filter(key=migration.SKILL_KEY).count() == 1
    assert ItemTemplate.objects.filter(key=migration.BOOK_KEY).count() == 1
    for field, value in migration.SKILL_DEFAULTS.items():
        assert getattr(skill, field) == value
    for field, value in migration.BOOK_DEFAULTS.items():
        assert getattr(book, field) == value
    assert book.image.name == "items/existing_skill3.webp"
    unrelated.refresh_from_db()
    assert unrelated.effect_payload == {"x": 1}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "force,intellect,level,allowed",
    [
        (400, 400, 80, True),
        (399, 400, 80, False),
        (400, 399, 80, False),
        (400, 400, 79, False),
    ],
)
def test_shenyi_poxu_learning_consumes_one_book_only_after_requirements_pass(
    django_user_model,
    force,
    intellect,
    level,
    allowed,
):
    from core.exceptions import GuestNotRequirementError
    from gameplay.models import InventoryItem, ItemTemplate
    from guests.models import GuestSkill, Skill
    from guests.services.skills import learn_guest_skill
    from tests.test_equipment_template_sync import _create_guest

    manor, guest = _create_guest(django_user_model, f"shenyi_{force}_{intellect}_{level}")
    guest.force, guest.intellect, guest.level = force, intellect, level
    guest.save(update_fields=["force", "intellect", "level"])
    skill, _ = Skill.objects.update_or_create(key=migration.SKILL_KEY, defaults=deepcopy(migration.SKILL_DEFAULTS))
    book, _ = ItemTemplate.objects.update_or_create(key=migration.BOOK_KEY, defaults=deepcopy(migration.BOOK_DEFAULTS))
    inventory = InventoryItem.objects.create(manor=manor, template=book, quantity=2)
    if allowed:
        learned = learn_guest_skill(guest, skill, inventory)
        assert learned.source == GuestSkill.Source.BOOK
        assert learned.skill_id == skill.pk
    else:
        with pytest.raises(GuestNotRequirementError):
            learn_guest_skill(guest, skill, inventory)
        assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()
    inventory.refresh_from_db()
    assert inventory.quantity == (1 if allowed else 2)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "naked_force,naked_intellect,allowed",
    [
        (280, 280, True),
        (279, 280, False),
        (280, 279, False),
    ],
)
def test_shenyi_poxu_retains_purple_book_non_equipment_70_percent_gate(
    django_user_model,
    naked_force,
    naked_intellect,
    allowed,
):
    from core.exceptions import GuestNotRequirementError
    from gameplay.models import InventoryItem, ItemTemplate
    from guests.models import GearItem, GearTemplate, GuestSkill, Skill
    from guests.services.skills import learn_guest_skill
    from tests.test_equipment_template_sync import _create_guest

    manor, guest = _create_guest(django_user_model, f"shenyi_naked_{naked_force}_{naked_intellect}")
    guest.force = guest.intellect = 400
    guest.level = 80
    guest.save(update_fields=["force", "intellect", "level"])
    equipment = GearTemplate.objects.create(
        key="shenyi_gate_gear",
        name="学习门槛测试装备",
        slot="weapon",
        extra_stats={"force": 400 - naked_force, "intellect": 400 - naked_intellect},
    )
    GearItem.objects.create(manor=manor, guest=guest, template=equipment)
    skill, _ = Skill.objects.update_or_create(key=migration.SKILL_KEY, defaults=deepcopy(migration.SKILL_DEFAULTS))
    book, _ = ItemTemplate.objects.update_or_create(key=migration.BOOK_KEY, defaults=deepcopy(migration.BOOK_DEFAULTS))
    inventory = InventoryItem.objects.create(manor=manor, template=book, quantity=2)
    if allowed:
        assert learn_guest_skill(guest, skill, inventory).source == GuestSkill.Source.BOOK
    else:
        with pytest.raises(GuestNotRequirementError):
            learn_guest_skill(guest, skill, inventory)
        assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()
    inventory.refresh_from_db()
    assert inventory.quantity == (1 if allowed else 2)
