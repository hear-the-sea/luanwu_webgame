import pytest

from core.exceptions import GuestAlreadyOwnedError, GuestCapacityFullError, InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate
from tests.guest_summon_card.support import make_edward_template, make_pubayi_template


@pytest.mark.django_db
def test_summon_card_rolls_blue(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="summon_blue", password="pass123")
    manor = ensure_manor(user)

    blue = make_pubayi_template("pubayi_blue_test", "blue")
    green = make_pubayi_template("pubayi_green_test", "green")

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_test",
        name="蒲巴乙门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [
                {"template_key": blue.key, "weight": 10},
                {"template_key": green.key, "weight": 90},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.05)
    payload = use_inventory_item(item)

    assert payload["获得门客"] == "蒲巴乙"
    assert payload["稀有度"] == "蓝"
    assert manor.guests.filter(template__key=blue.key).count() == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_summon_card_rolls_green(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="summon_green", password="pass123")
    manor = ensure_manor(user)

    blue = make_pubayi_template("pubayi_blue_test2", "blue")
    green = make_pubayi_template("pubayi_green_test2", "green")

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_test2",
        name="蒲巴乙门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [
                {"template_key": blue.key, "weight": 10},
                {"template_key": green.key, "weight": 90},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.50)
    payload = use_inventory_item(item)

    assert payload["获得门客"] == "蒲巴乙"
    assert payload["稀有度"] == "绿"
    assert manor.guests.filter(template__key=green.key).count() == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_summon_card_respects_guest_capacity(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="summon_full", password="pass123")
    manor = ensure_manor(user)

    blue = make_pubayi_template("pubayi_blue_test3", "blue")
    green = make_pubayi_template("pubayi_green_test3", "green")

    capacity = manor.guest_capacity
    for _ in range(capacity):
        Guest.objects.create(
            manor=manor,
            template=green,
            force=80,
            intellect=90,
            defense_stat=70,
            agility=75,
            luck=60,
            loyalty=60,
            gender="male",
            morality=60,
        )

    template = ItemTemplate.objects.create(
        key="pubayi_guest_card_test3",
        name="蒲巴乙门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "choices": [
                {"template_key": blue.key, "weight": 10},
                {"template_key": green.key, "weight": 90},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.05)
    with pytest.raises(GuestCapacityFullError):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_summon_card_rejects_duplicate_unique_guest(django_user_model):
    user = django_user_model.objects.create_user(username="summon_unique_guard", password="pass123")
    manor = ensure_manor(user)
    unique_key = "test_unique_guest_guard_001"

    template = GuestTemplate.objects.create(
        key=unique_key,
        name="潘凤",
        archetype="military",
        rarity="green",
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
    Guest.objects.create(
        manor=manor,
        template=template,
        force=80,
        intellect=90,
        defense_stat=70,
        agility=75,
        luck=60,
        loyalty=60,
        gender="male",
        morality=60,
    )

    card_template = ItemTemplate.objects.create(
        key="panfeng_guest_card_unique_guard_test",
        name="潘凤门客卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "exclusive_template_keys": [
                unique_key,
                f"{unique_key}_blue",
                f"{unique_key}_purple",
            ],
            "choices": [
                {"template_key": unique_key, "weight": 100},
            ],
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=card_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(GuestAlreadyOwnedError, match="不可重复获得"):
        use_inventory_item(item)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_summon_card_consumes_required_items_on_success(django_user_model):
    user = django_user_model.objects.create_user(username="summon_required_items_ok", password="pass123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="orig_zhu_yingtai_test",
        name="祝英台",
        archetype="civil",
        rarity="purple",
        base_attack=80,
        base_intellect=90,
        base_defense=70,
        base_agility=75,
        base_luck=60,
        base_hp=1000,
        default_gender="female",
        default_morality=60,
        recruitable=False,
    )
    good_card_template = ItemTemplate.objects.create(
        key="haorenka_test",
        name="好人卡",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        is_usable=False,
        rarity="green",
    )
    scroll_template = ItemTemplate.objects.create(
        key="zhuyingtai_guest_scroll_test",
        name="祝英台门客合成卷轴",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"haorenka_test": 360},
            "exclusive_template_keys": [template.key],
            "choices": [{"template_key": template.key, "weight": 100}],
        },
    )
    good_cards = InventoryItem.objects.create(
        manor=manor,
        template=good_card_template,
        quantity=400,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    scroll = InventoryItem.objects.create(
        manor=manor,
        template=scroll_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    payload = use_inventory_item(scroll)

    assert payload["获得门客"] == "祝英台"
    assert manor.guests.filter(template__key=template.key).count() == 1
    good_cards.refresh_from_db()
    assert good_cards.quantity == 40
    assert not InventoryItem.objects.filter(pk=scroll.pk).exists()


@pytest.mark.django_db
def test_summon_card_keeps_scroll_when_required_items_insufficient(django_user_model):
    user = django_user_model.objects.create_user(username="summon_required_items_fail", password="pass123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="orig_liang_shanbo_test",
        name="梁山伯",
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
    good_card_template = ItemTemplate.objects.create(
        key="haorenka_test_short",
        name="好人卡",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        is_usable=False,
        rarity="green",
    )
    scroll_template = ItemTemplate.objects.create(
        key="liangshanbo_guest_scroll_test",
        name="梁山伯门客合成卷轴",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"haorenka_test_short": 210},
            "exclusive_template_keys": [template.key],
            "choices": [{"template_key": template.key, "weight": 100}],
        },
    )
    good_cards = InventoryItem.objects.create(
        manor=manor,
        template=good_card_template,
        quantity=100,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    scroll = InventoryItem.objects.create(
        manor=manor,
        template=scroll_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(InsufficientStockError):
        use_inventory_item(scroll)

    scroll.refresh_from_db()
    good_cards.refresh_from_db()
    assert scroll.quantity == 1
    assert good_cards.quantity == 100
    assert manor.guests.filter(template__key=template.key).count() == 0


@pytest.mark.django_db
def test_edward_blue_scroll_consumes_gold_bars_and_summons_guest(django_user_model):
    user = django_user_model.objects.create_user(username="edward_scroll_blue", password="pass123")
    manor = ensure_manor(user)

    blue_edward = make_edward_template("orig_edward_blue_test", "blue")
    gold_bar_template = ItemTemplate.objects.create(
        key="gold_bar",
        name="金条",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        is_usable=False,
        rarity="orange",
    )
    scroll_template = ItemTemplate.objects.create(
        key="edward_blue_guest_scroll_test",
        name="蓝色爱德华召唤卷轴",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"gold_bar": 50},
            "choices": [{"template_key": blue_edward.key, "weight": 100}],
        },
    )
    gold_bars = InventoryItem.objects.create(
        manor=manor,
        template=gold_bar_template,
        quantity=80,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    scroll = InventoryItem.objects.create(
        manor=manor,
        template=scroll_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    payload = use_inventory_item(scroll)

    assert payload["获得门客"] == "爱德华"
    assert payload["稀有度"] == "蓝"
    gold_bars.refresh_from_db()
    assert gold_bars.quantity == 30
    assert manor.guests.filter(template__key=blue_edward.key).count() == 1
    assert not InventoryItem.objects.filter(pk=scroll.pk).exists()


@pytest.mark.django_db
def test_edward_purple_scroll_consumes_gold_bars_and_summons_guest(django_user_model):
    user = django_user_model.objects.create_user(username="edward_scroll_purple", password="pass123")
    manor = ensure_manor(user)

    purple_edward = make_edward_template("orig_edward_purple_test", "purple")
    gold_bar_template = ItemTemplate.objects.create(
        key="gold_bar",
        name="金条",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        is_usable=False,
        rarity="orange",
    )
    scroll_template = ItemTemplate.objects.create(
        key="edward_purple_guest_scroll_test",
        name="紫色爱德华召唤卷轴",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"gold_bar": 150},
            "choices": [{"template_key": purple_edward.key, "weight": 100}],
        },
    )
    gold_bars = InventoryItem.objects.create(
        manor=manor,
        template=gold_bar_template,
        quantity=200,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    scroll = InventoryItem.objects.create(
        manor=manor,
        template=scroll_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    payload = use_inventory_item(scroll)

    assert payload["获得门客"] == "爱德华"
    assert payload["稀有度"] == "紫"
    gold_bars.refresh_from_db()
    assert gold_bars.quantity == 50
    assert manor.guests.filter(template__key=purple_edward.key).count() == 1
    assert not InventoryItem.objects.filter(pk=scroll.pk).exists()


@pytest.mark.django_db
def test_edward_scroll_allows_repeatable_summons(django_user_model):
    user = django_user_model.objects.create_user(username="edward_scroll_repeatable", password="pass123")
    manor = ensure_manor(user)

    blue_edward = make_edward_template("orig_edward_blue_repeatable_test", "blue")
    gold_bar_template = ItemTemplate.objects.create(
        key="gold_bar",
        name="金条",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        is_usable=False,
        rarity="orange",
    )
    scroll_template = ItemTemplate.objects.create(
        key="edward_blue_guest_scroll_repeatable_test",
        name="蓝色爱德华召唤卷轴",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"gold_bar": 50},
            "choices": [{"template_key": blue_edward.key, "weight": 100}],
        },
    )
    gold_bars = InventoryItem.objects.create(
        manor=manor,
        template=gold_bar_template,
        quantity=120,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    scroll = InventoryItem.objects.create(
        manor=manor,
        template=scroll_template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    use_inventory_item(scroll)
    scroll.refresh_from_db()
    use_inventory_item(scroll)

    gold_bars.refresh_from_db()
    assert gold_bars.quantity == 20
    assert manor.guests.filter(template__key=blue_edward.key).count() == 2


@pytest.mark.django_db
def test_edward_scroll_keeps_scroll_when_gold_bars_insufficient(django_user_model):
    user = django_user_model.objects.create_user(username="edward_scroll_gold_fail", password="pass123")
    manor = ensure_manor(user)

    blue_edward = make_edward_template("orig_edward_blue_insufficient_test", "blue")
    gold_bar_template = ItemTemplate.objects.create(
        key="gold_bar",
        name="金条",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        is_usable=False,
        rarity="orange",
    )
    scroll_template = ItemTemplate.objects.create(
        key="edward_blue_guest_scroll_insufficient_test",
        name="蓝色爱德华召唤卷轴",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "summon_guest",
            "required_items": {"gold_bar": 50},
            "choices": [{"template_key": blue_edward.key, "weight": 100}],
        },
    )
    gold_bars = InventoryItem.objects.create(
        manor=manor,
        template=gold_bar_template,
        quantity=20,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    scroll = InventoryItem.objects.create(
        manor=manor,
        template=scroll_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(InsufficientStockError):
        use_inventory_item(scroll)

    scroll.refresh_from_db()
    gold_bars.refresh_from_db()
    assert scroll.quantity == 1
    assert gold_bars.quantity == 20
    assert manor.guests.filter(template__key=blue_edward.key).count() == 0
