import logging

import pytest

from core.exceptions import ItemNotConfiguredError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import GearSlot, GearTemplate


@pytest.mark.django_db
def test_loot_box_logs_and_tracks_skipped_bonus_items(monkeypatch, caplog, django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_skip_bonus", password="pass123")
    manor = ensure_manor(user)
    initial_silver = manor.silver

    template = ItemTemplate.objects.create(
        key="loot_box_skip_bonus_test",
        name="测试宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "resources": {"silver": 100},
            "skill_book_chance": 1,
            "skill_book_keys": ["missing_bonus_item"],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.choice", lambda keys: keys[0])

    def _raise_bonus_item_error(*_args, **_kwargs):
        raise ItemNotFoundError("bonus item template missing")

    monkeypatch.setattr(
        "gameplay.services.inventory.use.add_item_to_inventory",
        _raise_bonus_item_error,
    )

    with caplog.at_level(logging.WARNING):
        payload = use_inventory_item(item)

    manor.refresh_from_db()
    assert manor.silver == initial_silver + 100
    assert payload["skipped_bonus_items"] == ["missing_bonus_item"]
    assert any("loot box bonus item grant skipped" in rec.getMessage() for rec in caplog.records)
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_resource_pack_non_dict_effect_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="resource_pack_invalid_payload_shape", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="resource_pack_invalid_payload_shape_test",
        name="坏结构资源包",
        effect_type=ItemTemplate.EffectType.RESOURCE_PACK,
        is_usable=True,
        effect_payload=["silver", 100],
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="effect_payload 配置异常"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_resource_pack_invalid_resource_amount_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="resource_pack_invalid_resource_amount", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="resource_pack_invalid_resource_amount_test",
        name="坏数量资源包",
        effect_type=ItemTemplate.EffectType.RESOURCE_PACK,
        is_usable=True,
        effect_payload={"silver": True},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="effect_payload 配置异常"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_resource_pack_malformed_grant_result_raises_assertion_error(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="resource_pack_bad_grant_result", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="resource_pack_bad_grant_result_test",
        name="坏返回资源包",
        effect_type=ItemTemplate.EffectType.RESOURCE_PACK,
        is_usable=True,
        effect_payload={"silver": 100},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr(
        "gameplay.services.inventory.use.grant_resources_locked",
        lambda *_args, **_kwargs: ({"silver": "bad"}, {}),
    )

    with pytest.raises(AssertionError, match="invalid inventory resource grant result amount"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_work_loot_box_grants_random_silver_and_single_gear_drop(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="work_loot_box_logic", password="pass123")
    manor = ensure_manor(user)
    initial_silver = manor.silver
    initial_target_gear_count = manor.gears.filter(template__key__in=["work_loot_gear_a", "work_loot_gear_b"]).count()

    gear_a = GearTemplate.objects.create(
        key="work_loot_gear_a",
        name="测试头盔A",
        slot=GearSlot.HELMET,
        rarity="green",
    )
    GearTemplate.objects.create(
        key="work_loot_gear_b",
        name="测试头盔B",
        slot=GearSlot.HELMET,
        rarity="green",
    )
    ItemTemplate.objects.create(
        key="work_loot_skill_book_a",
        name="测试技能书A",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        is_usable=False,
        effect_payload={"skill_key": "test_skill_a", "skill_name": "测试术法"},
        rarity="green",
    )

    chest_template = ItemTemplate.objects.create(
        key="work_loot_box_test",
        name="打工宝箱（小）测试",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "silver_min": 8000,
            "silver_max": 9000,
            "gear_chance": 0.1,
            "gear_keys": ["work_loot_gear_a", "work_loot_gear_b"],
            "skill_book_chance": 0.1,
            "skill_book_keys": ["work_loot_skill_book_a"],
        },
    )
    chest = InventoryItem.objects.create(
        manor=manor,
        template=chest_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    roll_iter = iter([0.01, 0.01])
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: next(roll_iter))
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.choice", lambda seq: seq[0])
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.randint", lambda _a, _b: 8888)

    payload = use_inventory_item(chest)

    manor.refresh_from_db()
    assert manor.silver == initial_silver + 8888
    gained_gears = manor.gears.filter(template__key__in=["work_loot_gear_a", "work_loot_gear_b"])
    assert gained_gears.count() == initial_target_gear_count + 1
    assert gained_gears.filter(template_id=gear_a.id).exists()

    skill_book_entry = InventoryItem.objects.filter(
        manor=manor,
        template__key="work_loot_skill_book_a",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).first()
    assert skill_book_entry is not None
    assert skill_book_entry.quantity == 1

    rewards = payload["rewards"]
    assert "银两+8888" in rewards
    assert len([entry for entry in rewards if entry.startswith("装备【")]) == 1
    assert len([entry for entry in rewards if entry.startswith("技能书【")]) == 1
    assert payload["skipped_bonus_items"] == []
    assert not InventoryItem.objects.filter(pk=chest.pk).exists()


@pytest.mark.django_db
def test_loot_box_weighted_gear_choices_select_weighted_gear(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="weighted_loot_box_logic", password="pass123")
    manor = ensure_manor(user)

    target_gear = GearTemplate.objects.create(
        key="weighted_loot_gear_a",
        name="权重测试头盔A",
        slot=GearSlot.HELMET,
        rarity="green",
    )
    GearTemplate.objects.create(
        key="weighted_loot_gear_b",
        name="权重测试头盔B",
        slot=GearSlot.HELMET,
        rarity="green",
    )

    chest_template = ItemTemplate.objects.create(
        key="weighted_loot_box_test",
        name="权重装备宝箱测试",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "gear_chance": 1,
            "gear_choices": [
                {"item_key": "weighted_loot_gear_a", "weight": 90},
                {"item_key": "weighted_loot_gear_b", "weight": 10},
            ],
        },
    )
    chest = InventoryItem.objects.create(
        manor=manor,
        template=chest_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.uniform", lambda _a, _b: 1.0)

    payload = use_inventory_item(chest)

    gained_gears = manor.gears.filter(template__key__in=["weighted_loot_gear_a", "weighted_loot_gear_b"])
    assert gained_gears.count() == 1
    assert gained_gears.filter(template_id=target_gear.id).exists()
    assert any(entry == f"装备【{target_gear.name}】" for entry in payload["rewards"])


@pytest.mark.django_db
def test_loot_box_weighted_gear_choices_grant_inventory_equipment_when_template_not_materialized(
    monkeypatch, django_user_model
):
    user = django_user_model.objects.create_user(username="weighted_loot_box_item_gear", password="pass123")
    manor = ensure_manor(user)
    target_template = ItemTemplate.objects.create(
        key="weighted_inventory_gear_a",
        name="权重测试装备道具A",
        effect_type="equip_helmet",
        is_usable=False,
        effect_payload={"hp": 10},
        rarity="green",
    )
    ItemTemplate.objects.create(
        key="weighted_inventory_gear_b",
        name="权重测试装备道具B",
        effect_type="equip_helmet",
        is_usable=False,
        effect_payload={"hp": 20},
        rarity="green",
    )
    chest_template = ItemTemplate.objects.create(
        key="weighted_inventory_gear_box_test",
        name="权重装备道具箱测试",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "gear_chance": 1,
            "gear_choices": [
                {"item_key": "weighted_inventory_gear_a", "weight": 90},
                {"item_key": "weighted_inventory_gear_b", "weight": 10},
            ],
        },
    )
    chest = InventoryItem.objects.create(
        manor=manor,
        template=chest_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.uniform", lambda _a, _b: 1.0)

    payload = use_inventory_item(chest)

    inventory_entry = InventoryItem.objects.get(
        manor=manor,
        template=target_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inventory_entry.quantity == 1
    assert manor.gears.filter(template__key="weighted_inventory_gear_a").count() == 0
    assert any(entry == f"装备【{target_template.name}】" for entry in payload["rewards"])


@pytest.mark.django_db
def test_loot_box_weighted_skill_book_choices_select_weighted_book(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="weighted_skill_book_box", password="pass123")
    manor = ensure_manor(user)
    target_book = ItemTemplate.objects.create(
        key="weighted_skill_book_a",
        name="权重测试技能书A",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        is_usable=False,
        effect_payload={"skill_key": "weighted_skill_a", "skill_name": "权重术法A"},
        rarity="green",
    )
    ItemTemplate.objects.create(
        key="weighted_skill_book_b",
        name="权重测试技能书B",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        is_usable=False,
        effect_payload={"skill_key": "weighted_skill_b", "skill_name": "权重术法B"},
        rarity="green",
    )
    chest_template = ItemTemplate.objects.create(
        key="weighted_skill_book_box_test",
        name="权重技能书箱测试",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "skill_book_chance": 1,
            "skill_book_choices": [
                {"item_key": "weighted_skill_book_a", "weight": 90},
                {"item_key": "weighted_skill_book_b", "weight": 10},
            ],
        },
    )
    chest = InventoryItem.objects.create(
        manor=manor,
        template=chest_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.uniform", lambda _a, _b: 1.0)

    payload = use_inventory_item(chest)

    skill_book_entry = InventoryItem.objects.get(
        manor=manor,
        template=target_book,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert skill_book_entry.quantity == 1
    assert any(entry == f"技能书【{target_book.name}】" for entry in payload["rewards"])


@pytest.mark.django_db
def test_loot_box_malformed_silver_grant_result_raises_assertion_error(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_bad_silver_result", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_bad_silver_result_test",
        name="坏银两返回宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "silver_min": 100,
            "silver_max": 100,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.randint", lambda _a, _b: 100)
    monkeypatch.setattr(
        "gameplay.services.inventory.use.grant_resources_locked",
        lambda *_args, **_kwargs: ({"silver": "bad"}, {}),
    )

    with pytest.raises(AssertionError, match="invalid inventory resource grant result amount"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
