import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

from core.exceptions import GuestItemConfigurationError, GuestNotRequirementError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.guest_items import use_soul_container
from gameplay.services.inventory.soul_fusion_helpers import SOUL_FUSION_RESULT_CONFIG
from guests.models import GearItem, GearSlot, Guest, GuestStatus, GuestTemplate
from guests.services.equipment import ensure_inventory_gears, equip_guest, unequip_guest_item
from tests.inventory_guest_items.support_upgrade import (
    _attach_soul_fusion_gear_state,
    _prepare_soul_container_case,
    _SoulFusionFixedRng,
)


@pytest.fixture
def temporary_item_image_storage(tmp_path, monkeypatch):
    image_field = ItemTemplate._meta.get_field("image")
    monkeypatch.setattr(image_field, "storage", FileSystemStorage(location=tmp_path))


def test_soul_fusion_ornament_balance_ranges_are_raised_for_each_rarity():
    expected = {
        "green": {
            "stat_total_range": (47, 59),
            "hp_range": (135, 215),
            "stat_floor": {"force": 13, "intellect": 12, "agility": 13, "luck": 10},
        },
        "blue": {
            "stat_total_range": (65, 81),
            "hp_range": (215, 325),
            "stat_floor": {"force": 15, "intellect": 13, "agility": 15, "luck": 11},
        },
        "purple": {
            "stat_total_range": (87, 105),
            "hp_range": (305, 425),
            "stat_floor": {"force": 17, "intellect": 15, "agility": 17, "luck": 13},
        },
    }

    actual = {
        rarity: {
            "stat_total_range": config["stat_total_range"],
            "hp_range": config["hp_range"],
            "stat_floor": config["stat_floor"],
        }
        for rarity, config in SOUL_FUSION_RESULT_CONFIG.items()
    }

    assert actual == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("guest_rarity", "expected_image"),
    [
        ("green", "items/yuhaitang.webp"),
        ("blue", "items/beiming.webp"),
        ("purple", "items/chixue.webp"),
    ],
)
def test_soul_fusion_ornament_saves_configured_image(
    django_user_model,
    guest_rarity,
    expected_image,
    temporary_item_image_storage,
):
    manor, guest, item = _prepare_soul_container_case(
        django_user_model,
        f"image_{guest_rarity}",
        guest_rarity=guest_rarity,
        archetype="military",
        level=30,
        force=180,
        intellect=120,
        defense=150,
        agility=140,
        luck=80,
    )

    result = use_soul_container(manor, item, guest.id)

    template = InventoryItem.objects.select_related("template").get(pk=result["generated_item_id"]).template
    assert template.image.name == expected_image
    with Image.open(template.image.path) as saved_image:
        assert saved_image.format == "WEBP"
        assert saved_image.size == (200, 200)


@pytest.mark.django_db
def test_use_soul_container_generates_green_ornament_with_military_bias_and_returns_gear(
    monkeypatch,
    django_user_model,
    temporary_item_image_storage,
):
    manor, guest, item = _prepare_soul_container_case(
        django_user_model,
        "military_green",
        guest_rarity="green",
        archetype="military",
        level=80,
        force=240,
        intellect=128,
        defense=176,
        agility=190,
        luck=72,
    )
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.inventory_random.Random", lambda: _SoulFusionFixedRng()
    )

    gear_item_template = _attach_soul_fusion_gear_state(
        guest,
        manor=manor,
        key="soul_fusion_returned_gear",
        name="灵魂融合测试佩剑",
        extra_stats={"force": 120, "agility": 30},
    )

    result = use_soul_container(manor, item, guest.id)

    generated_item = InventoryItem.objects.select_related("template").get(pk=result["generated_item_id"])
    payload = generated_item.template.effect_payload

    assert generated_item.template.name == "玉海棠"
    assert generated_item.template.rarity == "green"
    assert generated_item.template.effect_type == "equip_ornament"
    assert set(payload.keys()) == {"hp", "force", "intellect", "agility", "luck"}
    assert 47 <= payload["force"] + payload["intellect"] + payload["agility"] + payload["luck"] <= 59
    assert 135 <= payload["hp"] <= 215
    assert payload["force"] > payload["intellect"]
    assert payload["agility"] >= payload["luck"]
    assert not Guest.objects.filter(pk=guest.pk).exists()
    assert not InventoryItem.objects.filter(pk=item.pk).exists()
    returned_gear = InventoryItem.objects.get(manor=manor, template=gear_item_template)
    assert returned_gear.quantity == 1
    assert "玉海棠" in result["_message"]
    assert "装备已归还仓库（1件）" in result["_message"]


@pytest.mark.django_db
def test_use_soul_container_ignores_equipment_and_set_bonuses_when_rolling_stats(
    monkeypatch,
    django_user_model,
    temporary_item_image_storage,
):
    base_kwargs = {
        "guest_rarity": "purple",
        "archetype": "military",
        "level": 92,
        "force": 260,
        "intellect": 148,
        "defense": 188,
        "agility": 176,
        "luck": 98,
    }
    equipped_manor, equipped_guest, equipped_item = _prepare_soul_container_case(
        django_user_model,
        "equipped_compare",
        **base_kwargs,
    )
    plain_manor, plain_guest, plain_item = _prepare_soul_container_case(
        django_user_model,
        "plain_compare",
        **base_kwargs,
    )

    returned_gear_template = _attach_soul_fusion_gear_state(
        equipped_guest,
        manor=equipped_manor,
        key="soul_fusion_compare_blade",
        name="灵魂融合对照佩刃",
        rarity="purple",
        extra_stats={"force": 48, "intellect": 16, "defense": 24, "agility": 14, "luck": 6},
        set_bonus={"force": 10, "agility": 7, "defense": 60},
    )

    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.inventory_random.Random", lambda: _SoulFusionFixedRng()
    )

    equipped_result = use_soul_container(equipped_manor, equipped_item, equipped_guest.id)
    plain_result = use_soul_container(plain_manor, plain_item, plain_guest.id)

    equipped_payload = (
        InventoryItem.objects.select_related("template")
        .get(pk=equipped_result["generated_item_id"])
        .template.effect_payload
    )
    plain_payload = (
        InventoryItem.objects.select_related("template")
        .get(pk=plain_result["generated_item_id"])
        .template.effect_payload
    )

    assert equipped_payload == plain_payload
    returned_gear = InventoryItem.objects.get(manor=equipped_manor, template=returned_gear_template)
    assert returned_gear.quantity == 1
    assert equipped_result["unequipped_count"] == 1


@pytest.mark.django_db
def test_use_soul_container_generated_ornament_can_be_equipped_and_unequipped(
    monkeypatch,
    django_user_model,
    temporary_item_image_storage,
):
    manor, guest, item = _prepare_soul_container_case(
        django_user_model,
        "equip_cycle",
        guest_rarity="blue",
        archetype="civil",
        level=86,
        force=158,
        intellect=244,
        defense=168,
        agility=162,
        luck=112,
    )
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.inventory_random.Random", lambda: _SoulFusionFixedRng()
    )

    result = use_soul_container(manor, item, guest.id)
    generated_item = InventoryItem.objects.select_related("template").get(pk=result["generated_item_id"])
    generated_stats = generated_item.template.effect_payload

    wearer_template = GuestTemplate.objects.create(
        key="soul_fusion_ornament_wearer_tpl",
        name="佩戴测试门客",
        archetype="civil",
        rarity="blue",
        base_attack=120,
        base_intellect=130,
        base_defense=100,
        base_agility=110,
        base_luck=80,
        base_hp=1400,
        default_gender="male",
        default_morality=60,
    )
    wearer = Guest.objects.create(
        manor=manor,
        template=wearer_template,
        level=50,
        force=180,
        intellect=220,
        defense_stat=170,
        agility=165,
        luck=95,
        status=GuestStatus.IDLE,
    )

    ensure_inventory_gears(manor, slot=GearSlot.ORNAMENT)
    free_gear = GearItem.objects.select_related("template").get(
        manor=manor,
        guest__isnull=True,
        template__key=generated_item.template.key,
    )

    equip_guest(free_gear, wearer)
    wearer.refresh_from_db()
    assert wearer.gear_items.filter(template__key=generated_item.template.key).exists()
    assert wearer.force == 180 + int(generated_stats.get("force", 0) or 0)
    assert wearer.intellect == 220 + int(generated_stats.get("intellect", 0) or 0)
    assert wearer.agility == 165 + int(generated_stats.get("agility", 0) or 0)
    assert wearer.luck == 95 + int(generated_stats.get("luck", 0) or 0)
    assert wearer.hp_bonus == int(generated_stats.get("hp", 0) or 0)
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=generated_item.template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()

    equipped_gear = wearer.gear_items.get(template__key=generated_item.template.key)
    unequip_guest_item(equipped_gear, wearer)
    wearer.refresh_from_db()
    returned_item = InventoryItem.objects.get(
        manor=manor,
        template=generated_item.template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert returned_item.quantity == 1
    assert wearer.gear_items.filter(template__key=generated_item.template.key).count() == 0
    assert wearer.force == 180
    assert wearer.intellect == 220
    assert wearer.agility == 165
    assert wearer.luck == 95
    assert wearer.hp_bonus == 0


@pytest.mark.django_db
def test_use_soul_container_generates_blue_ornament_with_civil_bias(
    monkeypatch,
    django_user_model,
    temporary_item_image_storage,
):
    manor, guest, item = _prepare_soul_container_case(
        django_user_model,
        "civil_blue",
        guest_rarity="blue",
        archetype="civil",
        level=88,
        force=150,
        intellect=255,
        defense=162,
        agility=166,
        luck=104,
    )
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.inventory_random.Random", lambda: _SoulFusionFixedRng()
    )

    result = use_soul_container(manor, item, guest.id)

    generated_item = InventoryItem.objects.select_related("template").get(pk=result["generated_item_id"])
    payload = generated_item.template.effect_payload

    assert generated_item.template.name == "北冥冰链"
    assert generated_item.template.rarity == "blue"
    assert 65 <= payload["force"] + payload["intellect"] + payload["agility"] + payload["luck"] <= 81
    assert 215 <= payload["hp"] <= 325
    assert payload["intellect"] > payload["force"]
    assert payload["intellect"] >= payload["agility"]
    assert all(payload[stat] > 0 for stat in ["force", "intellect", "agility", "luck"])
    assert not Guest.objects.filter(pk=guest.pk).exists()
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_use_soul_container_rejects_low_level_or_unsupported_rarity(django_user_model):
    manor, guest, item = _prepare_soul_container_case(
        django_user_model,
        "low_level",
        guest_rarity="green",
        archetype="military",
        level=29,
        force=180,
        intellect=110,
        defense=150,
        agility=140,
        luck=70,
    )

    with pytest.raises(GuestNotRequirementError, match="30级及以上"):
        use_soul_container(manor, item, guest.id)

    item.refresh_from_db()
    assert item.quantity == 1

    gray_template = GuestTemplate.objects.create(
        key="soul_container_gray_guest_tpl",
        name="灰色门客",
        archetype="civil",
        rarity="gray",
    )
    gray_guest = Guest.objects.create(
        manor=manor,
        template=gray_template,
        level=50,
        status=GuestStatus.IDLE,
    )

    with pytest.raises(GuestItemConfigurationError, match="绿色、蓝色或紫色门客"):
        use_soul_container(manor, item, gray_guest.id)

    item.refresh_from_db()
    assert item.quantity == 1
