import pytest

from core.exceptions import ItemNotConfiguredError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_loot_box_invalid_probability_config_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_prob", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_prob_test",
        name="坏概率宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "gear_chance": "bad",
            "gear_keys": ["work_loot_gear_a"],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_skill_book_probability_config_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_skill_prob", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_skill_prob_test",
        name="坏技能书概率宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "skill_book_chance": {"bad": "payload"},
            "skill_book_keys": ["missing_bonus_item"],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_resources_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_resources", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_resources_test",
        name="坏资源宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "resources": ["silver", 100],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_false_resources_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_false_resources", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_false_resources_test",
        name="坏布尔资源宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "resources": False,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_resource_amount_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_resource_amount", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_resource_amount_test",
        name="坏资源数量宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "resources": {"silver": True},
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_gear_keys_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_gear_keys", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_gear_keys_test",
        name="坏装备列表宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "gear_chance": 1,
            "gear_keys": "not-a-list",
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_gear_key_entry_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_gear_key_entry", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_gear_key_entry_test",
        name="坏装备条目宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "gear_chance": 1,
            "gear_keys": [""],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_skill_book_keys_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_skill_book_keys", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_skill_book_keys_test",
        name="坏技能书列表宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "skill_book_chance": 1,
            "skill_book_keys": "not-a-list",
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_invalid_skill_book_key_entry_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_invalid_skill_book_key_entry", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_invalid_skill_book_key_entry_test",
        name="坏技能书条目宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "skill_book_chance": 1,
            "skill_book_keys": [None],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_negative_silver_range_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_negative_silver_range", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_negative_silver_range_test",
        name="坏银两区间宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "silver_min": -1,
            "silver_max": 100,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_loot_box_reversed_silver_range_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="loot_box_reversed_silver_range", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="loot_box_reversed_silver_range_test",
        name="反向银两区间宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={
            "silver_min": 200,
            "silver_max": 100,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("case", "groups"),
    [
        ("mapping", {}),
        ("chance", [{"chance": "bad", "choices": [{"item_key": "reward", "weight": 1}]}]),
        (
            "range",
            [
                {
                    "chance": 1,
                    "min_quantity": 2,
                    "max_quantity": 1,
                    "choices": [{"item_key": "reward", "weight": 1}],
                }
            ],
        ),
        ("choices_empty", [{"chance": 1, "choices": []}]),
        ("choice_shape", [{"chance": 1, "choices": ["reward"]}]),
        ("weight", [{"chance": 1, "choices": [{"item_key": "reward", "weight": 0}]}]),
    ],
)
def test_loot_box_invalid_random_item_groups_raise_config_error(case, groups, django_user_model):
    user = django_user_model.objects.create_user(username=f"bad_random_groups_{case}", password="pass123")
    manor = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key=f"bad_random_groups_box_{case}",
        name="坏独立概率组宝箱",
        effect_type=ItemTemplate.EffectType.LOOT_BOX,
        is_usable=True,
        effect_payload={"random_item_groups": groups},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
