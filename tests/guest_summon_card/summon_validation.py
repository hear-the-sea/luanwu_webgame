import pytest

from core.exceptions import ItemNotConfiguredError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestTemplate
from tests.guest_summon_card.support import make_pubayi_template


@pytest.mark.django_db
def test_summon_card_invalid_choice_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="summon_invalid_choice", password="pass123")
    manor = ensure_manor(user)

    GuestTemplate.objects.create(
        key="pubayi_blue_invalid_choice",
        name="蒲巴乙",
        archetype="civil",
        rarity="blue",
        base_attack=80,
        base_intellect=90,
        base_defense=70,
        base_agility=75,
        base_luck=60,
        base_hp=1000,
        default_gender="male",
        default_morality=60,
        recruitable=False,
    )

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_choice",
        name="坏配置门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [
                {"template_key": "pubayi_blue_invalid_choice", "weight": "bad"},
            ],
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
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_invalid_required_items_payload_raises_config_error(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="summon_invalid_required_items", password="pass123")
    manor = ensure_manor(user)

    blue = make_pubayi_template("pubayi_blue_invalid_cost", "blue")

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_required_items",
        name="坏消耗门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [
                {"template_key": blue.key, "weight": 100},
            ],
            "required_items": {
                "": 1,
            },
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_non_string_choice_template_key_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="summon_choice_non_string_key", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_non_string_choice_key",
        name="坏模板键门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [
                {"template_key": 123, "weight": 100},
            ],
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
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_invalid_action_type_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="summon_invalid_action_type", password="pass123")
    manor = ensure_manor(user)
    template = make_pubayi_template("pubayi_invalid_action_type", "green")

    card_template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_action_type",
        name="坏 action 门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": True,
            "choices": [
                {"template_key": template.key, "weight": 100},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_non_positive_required_items_amount_raises_config_error(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="summon_required_items_zero", password="pass123")
    manor = ensure_manor(user)
    blue = make_pubayi_template("pubayi_blue_invalid_required_amount", "blue")

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_required_amount",
        name="坏数量门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [{"template_key": blue.key, "weight": 100}],
            "required_items": {blue.key: 0},
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_false_required_items_payload_raises_config_error(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="summon_required_items_false", password="pass123")
    manor = ensure_manor(user)
    blue = make_pubayi_template("pubayi_blue_invalid_required_false", "blue")

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_required_false",
        name="坏布尔消耗门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [{"template_key": blue.key, "weight": 100}],
            "required_items": False,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_non_dict_effect_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="summon_invalid_payload_shape", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_payload_shape",
        name="坏结构门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload=["summon_guest"],
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
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_invalid_exclusive_template_keys_payload_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="summon_invalid_exclusive_keys", password="pass123")
    manor = ensure_manor(user)
    template = make_pubayi_template("pubayi_invalid_exclusive_key_test", "green")

    card_template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_exclusive_keys",
        name="坏唯一门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "exclusive_template_keys": "not-a-list",
            "choices": [
                {"template_key": template.key, "weight": 100},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.count() == 0


@pytest.mark.django_db
def test_summon_card_invalid_exclusive_template_key_entry_raises_config_error(django_user_model):
    user = django_user_model.objects.create_user(username="summon_invalid_exclusive_key_entry", password="pass123")
    manor = ensure_manor(user)
    template = make_pubayi_template("pubayi_invalid_exclusive_entry_test", "green")

    card_template = ItemTemplate.objects.create(
        key="pubayi_guest_card_invalid_exclusive_entry",
        name="坏唯一门客条目卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "exclusive_template_keys": [""],
            "choices": [
                {"template_key": template.key, "weight": 100},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotConfiguredError, match="物品配置异常，请联系管理员"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.count() == 0
