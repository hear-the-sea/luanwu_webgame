import pytest

from core.exceptions import GuestItemConfigurationError
from gameplay.models import InventoryItem
from gameplay.services.inventory.guest_items import use_guest_rarity_upgrade_item
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
