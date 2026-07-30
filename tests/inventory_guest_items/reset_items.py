import pytest

from core.exceptions import GameError, GuestAllocationResetError, GuestNotIdleError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.guest_items import use_guest_rebirth_card, use_xidianka, use_xisuidan
from guests.models import GearItem, GearSlot, GearTemplate, GuestSkill, GuestStatus, Skill
from guests.services.equipment import apply_set_bonuses
from tests.inventory_guest_items.support_reset import (
    _prepare_rebirth_case,
    _prepare_xidianka_case,
    _prepare_xisuidan_case,
)


@pytest.mark.django_db
def test_use_xisuidan_keeps_total_when_reroll_is_worse(monkeypatch, django_user_model):
    manor, guest, item = _prepare_xisuidan_case(django_user_model, "worse")

    monkeypatch.setattr(
        "guests.utils.attribute_growth.allocate_level_up_attributes",
        lambda *_args, **_kwargs: {"force": 10, "intellect": 10, "defense": 10, "agility": 10},
    )

    result = use_xisuidan(manor, item, guest.id)
    guest.refresh_from_db()

    assert result["old_total"] == 400
    assert result["new_total"] == 400
    assert result["growth_diff"] == 0
    assert guest.force == 150
    assert guest.intellect == 150
    assert guest.defense_stat == 150
    assert guest.agility == 150
    assert guest.xisuidan_used == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_use_xisuidan_applies_better_growth(monkeypatch, django_user_model):
    manor, guest, item = _prepare_xisuidan_case(django_user_model, "better")

    monkeypatch.setattr(
        "guests.utils.attribute_growth.allocate_level_up_attributes",
        lambda *_args, **_kwargs: {"force": 120, "intellect": 120, "defense": 120, "agility": 120},
    )

    result = use_xisuidan(manor, item, guest.id)
    guest.refresh_from_db()

    assert result["old_total"] == 400
    assert result["new_total"] == 480
    assert result["growth_diff"] == 80
    assert guest.force == 170
    assert guest.intellect == 170
    assert guest.defense_stat == 170
    assert guest.agility == 170
    assert guest.xisuidan_used == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_use_xisuidan_rejects_non_idle_guest(django_user_model):
    manor, guest, item = _prepare_xisuidan_case(django_user_model, "non_idle")
    guest.status = GuestStatus.WORKING
    guest.save(update_fields=["status"])

    with pytest.raises(GuestNotIdleError, match="非空闲状态"):
        use_xisuidan(manor, item, guest.id)

    item.refresh_from_db()
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_guest_rebirth_card_resets_guest_progression_and_clears_gear(django_user_model):
    manor, guest, item = _prepare_rebirth_case(django_user_model, "ok")

    gear_template = GearTemplate.objects.create(
        key="rebirth_test_blade",
        name="重生测试佩刀",
        slot=GearSlot.WEAPON,
        rarity="green",
    )
    returned_item_template = ItemTemplate.objects.create(
        key="rebirth_test_blade",
        name="重生测试佩刀",
        effect_type="equip_weapon",
        rarity="green",
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)
    skill = Skill.objects.create(key="rebirth_test_skill", name="重生测试技能")
    GuestSkill.objects.create(guest=guest, skill=skill)

    result = use_guest_rebirth_card(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.level == 1
    assert guest.experience == 0
    assert guest.attribute_points == 0
    assert guest.attack_bonus == 0
    assert guest.defense_bonus == 0
    assert guest.hp_bonus == 0
    assert guest.training_target_level == 2
    assert guest.training_complete_at is not None
    assert guest.xisuidan_used == 0
    assert guest.allocated_force == 0
    assert guest.allocated_intellect == 0
    assert guest.allocated_defense == 0
    assert guest.allocated_agility == 0
    assert guest.status == GuestStatus.IDLE
    assert guest.current_hp == guest.max_hp
    assert guest.gear_items.count() == 0
    assert guest.guest_skills.count() == 0
    assert guest.initial_force == guest.force
    assert guest.initial_intellect == guest.intellect
    assert guest.initial_defense == guest.defense_stat
    assert guest.initial_agility == guest.agility
    assert result["old_level"] == 35
    assert result["unequipped_count"] == 1
    assert result["skills_cleared"] == 1
    assert "技能已清空（1个）" in result["_message"]
    assert "装备已卸下（1件）" in result["_message"]
    assert not InventoryItem.objects.filter(pk=item.pk).exists()
    returned_weapon = InventoryItem.objects.get(manor=manor, template=returned_item_template)
    assert returned_weapon.quantity == 1


@pytest.mark.django_db
def test_use_guest_rebirth_card_force_detach_fallback_keeps_success_on_known_unequip_error(
    monkeypatch, django_user_model
):
    manor, guest, item = _prepare_rebirth_case(django_user_model, "fallback_ok")

    gear_template = GearTemplate.objects.create(
        key="rebirth_fallback_blade",
        name="重生兜底佩刀",
        slot=GearSlot.WEAPON,
        rarity="green",
    )
    returned_item_template = ItemTemplate.objects.create(
        key="rebirth_fallback_blade",
        name="重生兜底佩刀",
        effect_type="equip_weapon",
        rarity="green",
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)
    guest.troop_capacity_bonus = 45
    guest.gear_set_bonus = {"troop_capacity": 45}
    guest.save(update_fields=["troop_capacity_bonus", "gear_set_bonus"])

    monkeypatch.setattr(
        "guests.services.equipment.unequip_guest_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(GameError(message="unequip blocked")),
    )

    result = use_guest_rebirth_card(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.gear_items.count() == 0
    assert guest.troop_capacity_bonus == 0
    assert guest.gear_set_bonus == {}
    assert result["unequipped_count"] == 1
    returned_weapon = InventoryItem.objects.get(manor=manor, template=returned_item_template)
    assert returned_weapon.quantity == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()

    apply_set_bonuses(guest)
    guest.refresh_from_db()
    assert guest.troop_capacity_bonus == 0


@pytest.mark.django_db
def test_use_guest_rebirth_card_programming_error_during_unequip_bubbles_up(monkeypatch, django_user_model):
    manor, guest, item = _prepare_rebirth_case(django_user_model, "fallback_bug")

    gear_template = GearTemplate.objects.create(
        key="rebirth_bug_blade",
        name="重生异常佩刀",
        slot=GearSlot.WEAPON,
        rarity="green",
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)

    monkeypatch.setattr(
        "guests.services.equipment.unequip_guest_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken unequip contract")),
    )

    with pytest.raises(AssertionError, match="broken unequip contract"):
        use_guest_rebirth_card(manor, item, guest.id)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.gear_items.count() == 1
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_xidianka_resets_allocated_points_and_refunds_attribute_points(django_user_model):
    manor, guest, item = _prepare_xidianka_case(django_user_model, "ok")

    result = use_xidianka(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.force == 154
    assert guest.intellect == 185
    assert guest.defense_stat == 147
    assert guest.agility == 156
    assert guest.attribute_points == 40
    assert guest.allocated_force == 0
    assert guest.allocated_intellect == 0
    assert guest.allocated_defense == 0
    assert guest.allocated_agility == 0
    assert result["total_returned"] == 33
    assert result["details"] == {"force": 12, "intellect": 9, "defense": 7, "agility": 5}
    assert "返还 33 属性点" in result["_message"]
    assert "武力-12" in result["_message"]
    assert "智力-9" in result["_message"]
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_use_xidianka_rejects_guest_without_allocated_points(django_user_model):
    manor, guest, item = _prepare_xidianka_case(django_user_model, "no_alloc")
    guest.attribute_points = 7
    guest.allocated_force = 0
    guest.allocated_intellect = 0
    guest.allocated_defense = 0
    guest.allocated_agility = 0
    guest.save(
        update_fields=[
            "attribute_points",
            "allocated_force",
            "allocated_intellect",
            "allocated_defense",
            "allocated_agility",
        ]
    )

    with pytest.raises(GuestAllocationResetError, match="无需使用洗点卡"):
        use_xidianka(manor, item, guest.id)
