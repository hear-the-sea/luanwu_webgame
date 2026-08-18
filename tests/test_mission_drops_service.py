from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from gameplay.models import InventoryItem, ItemTemplate, ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl.drops import (
    _get_or_create_skill_book_template,
    _infer_equipment_effect_type,
    _infer_equipment_effect_type_from_category,
    award_mission_drops,
    award_mission_drops_locked,
)
from guests.models import GearSlot, GearTemplate, Skill, SkillBook

User = get_user_model()


@pytest.mark.django_db
def test_award_mission_drops_grants_resources_and_items():
    user = User.objects.create_user(username="mission_drop_user", password="pass123")
    manor = ensure_manor(user)
    initial_silver = manor.silver
    item_template = ItemTemplate.objects.create(key="mission_drop_item", name="任务掉落道具")

    award_mission_drops(manor, {"silver": 30, "mission_drop_item": 2}, note="测试任务")

    manor.refresh_from_db()
    assert manor.silver == initial_silver + 30

    inv = InventoryItem.objects.get(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inv.quantity == 2

    event = ResourceEvent.objects.filter(
        manor=manor,
        resource_type=ResourceType.SILVER,
        reason=ResourceEvent.Reason.BATTLE_REWARD,
        note="测试任务",
    ).first()
    assert event is not None
    assert event.delta == 30


@pytest.mark.django_db
def test_award_mission_drops_ignores_warehouse_capacity():
    user = User.objects.create_user(username="mission_drop_capacity", password="pass123")
    manor = ensure_manor(user)
    item_template = ItemTemplate.objects.create(
        key="mission_drop_capacity_item",
        name="容量不足掉落",
        storage_space=2,
    )
    initial_silver = manor.silver

    award_mission_drops(
        manor,
        {"silver": 30, item_template.key: 1},
        note="无容量上限任务",
    )

    manor.refresh_from_db()
    assert manor.silver == initial_silver + 30
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 1
    )


@pytest.mark.django_db
def test_award_mission_drops_creates_skill_book_template_when_missing():
    user = User.objects.create_user(username="mission_drop_skillbook", password="pass123")
    manor = ensure_manor(user)

    skill = Skill.objects.create(key="mission_skill", name="任务技能")
    SkillBook.objects.create(key="mission_skill_book", name="任务技能书", skill=skill)

    assert ItemTemplate.objects.filter(key="mission_skill_book").exists() is False

    award_mission_drops(manor, {"mission_skill_book": 1}, note="技能书任务")

    created_template = ItemTemplate.objects.get(key="mission_skill_book")
    assert created_template.effect_type == ItemTemplate.EffectType.SKILL_BOOK
    assert created_template.effect_payload["skill_key"] == "mission_skill"
    assert created_template.effect_payload["skill_name"] == "任务技能"

    inv = InventoryItem.objects.get(
        manor=manor,
        template=created_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inv.quantity == 1


@pytest.mark.django_db
def test_award_mission_drops_locked_requires_atomic(monkeypatch):
    user = User.objects.create_user(username="mission_drop_locked", password="pass123")
    manor = ensure_manor(user)
    monkeypatch.setattr(
        "gameplay.services.missions_impl.drops.transaction.get_connection",
        lambda: SimpleNamespace(in_atomic_block=False),
    )

    with pytest.raises(RuntimeError, match="must be called inside transaction.atomic"):
        award_mission_drops_locked(manor, {"silver": 1}, note="locked契约")


@pytest.mark.django_db
def test_get_or_create_skill_book_template_recovers_from_integrity_error(monkeypatch):
    skill = Skill.objects.create(key="mission_skill_2", name="任务技能2")
    book = SkillBook.objects.create(key="mission_skill_book_2", name="任务技能书2", skill=skill)

    existing = ItemTemplate.objects.create(
        key="mission_skill_book_2",
        name="任务技能书2",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": skill.key, "skill_name": skill.name},
    )

    get_or_create_mock = Mock(side_effect=IntegrityError("duplicate key"))
    monkeypatch.setattr(
        "gameplay.services.missions_impl.drops.ItemTemplate.objects.get_or_create",
        get_or_create_mock,
    )

    resolved = _get_or_create_skill_book_template("mission_skill_book_2", book)
    assert resolved.pk == existing.pk


@pytest.mark.django_db
def test_award_mission_drops_rejects_negative_drop_amount():
    user = User.objects.create_user(username="mission_drop_negative_amount", password="pass123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid mission drop amount"):
        award_mission_drops(manor, {"silver": -1}, note="坏掉落")


@pytest.mark.django_db
def test_award_mission_drops_rejects_non_string_drop_key():
    user = User.objects.create_user(username="mission_drop_non_string_key", password="pass123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid mission drop key"):
        award_mission_drops(manor, {1: 1}, note="坏掉落key")


@pytest.mark.django_db
def test_award_mission_drops_rejects_bool_drop_amount():
    user = User.objects.create_user(username="mission_drop_bool_amount", password="pass123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid mission drop amount"):
        award_mission_drops(manor, {"silver": True}, note="坏掉落bool")


@pytest.mark.django_db
def test_award_mission_drops_rejects_missing_item_template():
    user = User.objects.create_user(username="mission_drop_missing_item_tpl", password="pass123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid mission drop item key"):
        award_mission_drops(manor, {"missing_drop_item": 1}, note="坏物品掉落")


@pytest.mark.django_db
def test_award_mission_drops_creates_equipment_item_template_from_gear_template():
    user = User.objects.create_user(username="mission_drop_missing_equip_item_tpl", password="pass123")
    manor = ensure_manor(user)

    gear_template = GearTemplate.objects.create(
        key="equip_mission_test_blade",
        name="任务掉落刀",
        slot=GearSlot.WEAPON,
        rarity="green",
    )
    assert ItemTemplate.objects.filter(key=gear_template.key).exists() is False

    award_mission_drops(manor, {gear_template.key: 1}, note="装备任务")

    created_template = ItemTemplate.objects.get(key=gear_template.key)
    assert created_template.name == gear_template.name
    assert created_template.effect_type == "equip_weapon"
    assert created_template.rarity == gear_template.rarity

    inv = InventoryItem.objects.get(
        manor=manor,
        template=created_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inv.quantity == 1


@pytest.mark.django_db
def test_award_mission_drops_creates_equipment_item_template_from_equipment_config():
    user = User.objects.create_user(username="mission_drop_config_equip_item_tpl", password="pass123")
    manor = ensure_manor(user)

    key = "equip_bupao"
    ItemTemplate.objects.filter(key=key).delete()

    award_mission_drops(manor, {key: 1}, note="配置装备任务")

    created_template = ItemTemplate.objects.get(key=key)
    assert created_template.effect_type == "equip_armor"

    inv = InventoryItem.objects.get(
        manor=manor,
        template=created_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inv.quantity == 1


def test_infer_equipment_effect_type_rejects_non_string_slot():
    with pytest.raises(AssertionError, match="invalid mission drop gear slot"):
        _infer_equipment_effect_type(True)


def test_infer_equipment_effect_type_from_category_rejects_non_string_category():
    with pytest.raises(AssertionError, match="invalid mission drop equipment category"):
        _infer_equipment_effect_type_from_category(1)
