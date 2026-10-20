import pytest

from core.exceptions import GuestItemConfigurationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.guest_items import use_guest_rarity_upgrade_item, use_guest_rebirth_card
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestSkill, GuestStatus, GuestTemplate, Skill
from guests.utils.attribute_growth import allocate_level_up_attributes
from tests.inventory_guest_items.support_upgrade import (
    _prepare_rarity_upgrade_blue_to_purple_case,
    _prepare_rarity_upgrade_case,
    _RangeSpyRng,
)


@pytest.mark.django_db
def test_use_guest_rarity_upgrade_item_switches_template_and_uses_blue_standard_growth_range(django_user_model):
    manor, guest, item, _green, blue = _prepare_rarity_upgrade_case(django_user_model, "ok")
    guest.level = 50
    guest.experience = 999
    guest.xisuidan_used = 6
    guest.allocated_force = 7
    guest.allocated_intellect = 8
    guest.allocated_defense = 9
    guest.allocated_agility = 10
    guest.troop_capacity_bonus = 45
    guest.gear_set_bonus = {"troop_capacity": 45}
    guest.save(
        update_fields=[
            "level",
            "experience",
            "xisuidan_used",
            "allocated_force",
            "allocated_intellect",
            "allocated_defense",
            "allocated_agility",
            "troop_capacity_bonus",
            "gear_set_bonus",
        ]
    )
    gear_template = GearTemplate.objects.create(
        key="rarity_upgrade_test_helmet",
        name="测试头盔",
        slot=GearSlot.HELMET,
        rarity="green",
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)
    skill = Skill.objects.create(key="rarity_upgrade_test_skill", name="测试技能")
    GuestSkill.objects.create(guest=guest, skill=skill)

    result = use_guest_rarity_upgrade_item(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.template_id == blue.id
    assert guest.rarity == "blue"
    assert guest.level == 1
    assert guest.experience == 0
    assert guest.training_target_level == 2
    assert guest.training_complete_at is not None
    assert guest.xisuidan_used == 0
    assert guest.allocated_force == 0
    assert guest.allocated_intellect == 0
    assert guest.allocated_defense == 0
    assert guest.allocated_agility == 0
    assert guest.troop_capacity_bonus == 0
    assert guest.gear_set_bonus == {}
    assert guest.gear_items.count() == 0
    assert guest.guest_skills.count() == 0
    assert result["new_rarity"] == "蓝"
    assert result["new_level"] == 1
    assert result["skills_cleared"] == 1
    assert "等级重置为1级" in result["_message"]
    assert "洗髓丹计数已重置" in result["_message"]
    assert "技能已清空（1个）" in result["_message"]
    assert not InventoryItem.objects.filter(pk=item.pk).exists()

    spy_rng = _RangeSpyRng()
    allocate_level_up_attributes(guest, levels=1, rng=spy_rng)
    assert spy_rng.last_range == (5, 9)


@pytest.mark.django_db
def test_use_guest_rarity_upgrade_item_rejects_unsupported_guest(django_user_model):
    manor, _guest, item, _green, _blue = _prepare_rarity_upgrade_case(django_user_model, "unsupported")
    other_template = GuestTemplate.objects.create(
        key="rarity_upgrade_other_tpl",
        name="其他门客",
        archetype="civil",
        rarity="green",
    )
    other_guest = Guest.objects.create(manor=manor, template=other_template, status=GuestStatus.IDLE)

    with pytest.raises(GuestItemConfigurationError, match="无法使用此升阶道具"):
        use_guest_rarity_upgrade_item(manor, item, other_guest.id)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_guest_rarity_upgrade_item_switches_blue_to_purple_and_uses_purple_standard_growth_range(django_user_model):
    manor, guest, item, _blue, purple = _prepare_rarity_upgrade_blue_to_purple_case(django_user_model, "ok")

    result = use_guest_rarity_upgrade_item(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.template_id == purple.id
    assert guest.rarity == "purple"
    assert guest.level == 1
    assert guest.xisuidan_used == 0
    assert result["new_rarity"] == "紫"
    assert "等级重置为1级" in result["_message"]
    assert not InventoryItem.objects.filter(pk=item.pk).exists()

    spy_rng = _RangeSpyRng()
    allocate_level_up_attributes(guest, levels=1, rng=spy_rng)
    assert spy_rng.last_range == (6, 11)


@pytest.mark.django_db
def test_green_to_blue_pill_creates_reusable_blue_template_and_uses_blue_standard_growth_range(django_user_model):
    user = django_user_model.objects.create_user(username="green_to_blue_pill", password="pass123")
    manor = ensure_manor(user)
    green_template = GuestTemplate.objects.create(
        key="green_to_blue_pill_guest",
        name="待转化门客",
        archetype="civil",
        rarity="green",
        base_attack=80,
        base_intellect=75,
        base_defense=70,
        base_agility=65,
        base_luck=50,
        base_hp=1200,
        growth_range=[3, 7],
        attribute_weights={"force": 20, "intellect": 40, "defense": 20, "agility": 20},
    )
    guest = Guest.objects.create(
        manor=manor,
        template=green_template,
        level=36,
        experience=999,
        force=220,
        intellect=280,
        defense_stat=180,
        agility=170,
        luck=60,
        status=GuestStatus.IDLE,
    )
    item_template = ItemTemplate.objects.create(
        key="green_to_blue_pill_item",
        name="绿转蓝丸子",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "upgrade_guest_rarity",
            "source_rarity": "green",
            "target_rarity": "blue",
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=item_template, quantity=2)

    result = use_guest_rarity_upgrade_item(manor, item, guest.id)

    guest.refresh_from_db()
    converted_template = guest.template
    assert converted_template.rarity == "blue"
    assert guest.rarity_pill_used is True
    assert converted_template.name == green_template.name
    assert converted_template.growth_range == []
    assert converted_template.attribute_weights == green_template.attribute_weights
    assert (
        guest.initial_force + guest.initial_intellect + guest.initial_defense + guest.initial_agility
        == green_template.base_attack
        + green_template.base_intellect
        + green_template.base_defense
        + green_template.base_agility
    )
    assert guest.level == 1
    assert guest.experience == 0
    assert guest.training_target_level == 2
    assert guest.training_complete_at is not None
    assert result["old_rarity"] == "绿"
    assert result["new_rarity"] == "蓝"
    assert item_template.inventory_entries.get(manor=manor).quantity == 1

    spy_rng = _RangeSpyRng()
    allocate_level_up_attributes(guest, levels=1, rng=spy_rng)
    assert spy_rng.last_range == (5, 9)

    second_guest = Guest.objects.create(manor=manor, template=green_template, status=GuestStatus.IDLE)
    second_item = item_template.inventory_entries.get(manor=manor)
    second_result = use_guest_rarity_upgrade_item(manor, second_item, second_guest.id)
    second_guest.refresh_from_db()
    assert second_guest.template_id == converted_template.id
    assert second_result["new_rarity"] == "蓝"


@pytest.mark.django_db
def test_blue_to_purple_pill_resets_initial_attributes_and_uses_purple_standard_growth(django_user_model):
    manor, guest, item, blue_template, _mapped_purple = _prepare_rarity_upgrade_blue_to_purple_case(
        django_user_model, "pill"
    )
    blue_template.base_attack = 80
    blue_template.base_intellect = 75
    blue_template.base_defense = 70
    blue_template.base_agility = 65
    blue_template.base_luck = 50
    blue_template.recruitable = True
    blue_template.save(
        update_fields=["base_attack", "base_intellect", "base_defense", "base_agility", "base_luck", "recruitable"]
    )
    item.template.effect_payload = {
        "action": "upgrade_guest_rarity",
        "source_rarity": "blue",
        "target_rarity": "purple",
    }
    item.template.save(update_fields=["effect_payload"])

    result = use_guest_rarity_upgrade_item(manor, item, guest.id)

    guest.refresh_from_db()
    converted_template = guest.template
    assert converted_template.rarity == "purple"
    assert guest.rarity_pill_used is True
    assert converted_template.name == blue_template.name
    assert converted_template.growth_range == []
    assert converted_template.base_attack == blue_template.base_attack
    assert converted_template.base_intellect == blue_template.base_intellect
    assert converted_template.base_defense == blue_template.base_defense
    assert converted_template.base_agility == blue_template.base_agility
    assert converted_template.base_luck == blue_template.base_luck
    assert (
        guest.initial_force + guest.initial_intellect + guest.initial_defense + guest.initial_agility
        == blue_template.base_attack
        + blue_template.base_intellect
        + blue_template.base_defense
        + blue_template.base_agility
    )
    assert guest.level == 1
    assert guest.experience == 0
    assert result["old_rarity"] == "蓝"
    assert result["new_rarity"] == "紫"
    assert not InventoryItem.objects.filter(pk=item.pk).exists()

    spy_rng = _RangeSpyRng()
    allocate_level_up_attributes(guest, levels=1, rng=spy_rng)
    assert spy_rng.last_range == (6, 11)


@pytest.mark.django_db
@pytest.mark.parametrize(("source_rarity", "target_rarity"), [("green", "blue"), ("blue", "purple")])
def test_rarity_pills_reject_non_recruitable_guests_without_consuming_or_resetting(
    django_user_model, source_rarity, target_rarity
):
    manor, guest, item, source, _target = _prepare_rarity_upgrade_case(django_user_model, source_rarity)
    source.rarity = source_rarity
    source.recruitable = False
    source.save(update_fields=["rarity", "recruitable"])
    item.template.effect_payload = {
        "action": "upgrade_guest_rarity",
        "source_rarity": source_rarity,
        "target_rarity": target_rarity,
    }
    item.template.save(update_fields=["effect_payload"])
    original_level = guest.level
    skill = Skill.objects.create(key="blocked_pill_skill", name="保留技能")
    GuestSkill.objects.create(guest=guest, skill=skill)

    with pytest.raises(GuestItemConfigurationError, match="非招募门客，无法使用"):
        use_guest_rarity_upgrade_item(manor, item, guest.id)

    item.refresh_from_db()
    guest.refresh_from_db()
    assert item.quantity == 1
    assert guest.template_id == source.pk
    assert guest.level == original_level
    assert guest.rarity_pill_used is False
    assert guest.guest_skills.filter(skill=skill).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("rebirth_after_conversion", [False, True])
def test_green_to_blue_pill_cannot_be_followed_by_blue_to_purple_even_after_rebirth(
    django_user_model, rebirth_after_conversion
):
    manor, guest, item, _source, _target = _prepare_rarity_upgrade_case(django_user_model, "once")
    item.template.effect_payload = {"action": "upgrade_guest_rarity", "source_rarity": "green", "target_rarity": "blue"}
    item.template.save(update_fields=["effect_payload"])
    use_guest_rarity_upgrade_item(manor, item, guest.id)

    if rebirth_after_conversion:
        rebirth = ItemTemplate.objects.create(
            key="pill_rebirth", name="重生卡", effect_payload={"action": "rebirth_guest"}
        )
        use_guest_rebirth_card(manor, InventoryItem.objects.create(manor=manor, template=rebirth, quantity=1), guest.id)

    blue_to_purple = ItemTemplate.objects.create(
        key="once_blue_to_purple_pill",
        name="蓝转紫丸子",
        effect_payload={"action": "upgrade_guest_rarity", "source_rarity": "blue", "target_rarity": "purple"},
    )
    second_item = InventoryItem.objects.create(manor=manor, template=blue_to_purple, quantity=2, bound_quantity=1)
    guest.refresh_from_db()
    converted_template_id = guest.template_id
    with pytest.raises(GuestItemConfigurationError, match="每名门客只能使用一次"):
        use_guest_rarity_upgrade_item(manor, second_item, guest.id, binding_mode="bound")

    second_item.refresh_from_db()
    guest.refresh_from_db()
    assert (second_item.quantity, second_item.bound_quantity) == (2, 1)
    assert guest.template_id == converted_template_id
    assert guest.rarity == "blue"
    assert guest.rarity_pill_used is True


@pytest.mark.django_db
@pytest.mark.parametrize("legacy_conversion", [False, True])
def test_rarity_pills_reject_previously_used_guests_with_recruitable_templates(django_user_model, legacy_conversion):
    manor, guest, item, source, _target = _prepare_rarity_upgrade_case(django_user_model, "used")
    if legacy_conversion:
        source.key = f"guest_rarity_conversion_blue_{source.pk}"
        source.rarity = "blue"
        source.save(update_fields=["key", "rarity"])
    else:
        guest.rarity_pill_used = True
        guest.save(update_fields=["rarity_pill_used"])
    item.template.effect_payload = {
        "action": "upgrade_guest_rarity",
        "source_rarity": source.rarity,
        "target_rarity": "purple",
    }
    item.template.save(update_fields=["effect_payload"])

    with pytest.raises(GuestItemConfigurationError, match="已使用过升阶丸子"):
        use_guest_rarity_upgrade_item(manor, item, guest.id)

    item.refresh_from_db()
    assert item.quantity == 1
