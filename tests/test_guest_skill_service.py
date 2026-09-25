from __future__ import annotations

from itertools import count

import pytest
from django.db import transaction

from core.exceptions import (
    GuestItemConfigurationError,
    GuestItemOwnershipError,
    GuestNotFoundError,
    GuestNotIdleError,
    GuestNotRequirementError,
    GuestSkillNotFoundError,
)
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestSkill, GuestStatus, GuestTemplate, Skill
from guests.services.equipment import equip_guest
from guests.services.skills import (
    collect_non_equipment_skill_attributes,
    collect_skill_requirements,
    collect_unmet_skill_requirements,
    forget_guest_skill,
    learn_guest_skill,
    learn_guest_skill_locked,
)

_COUNTER = count(1)


def _unique(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


def _create_guest(manor, **overrides) -> Guest:
    template = GuestTemplate.objects.create(
        key=_unique("skill_service_guest_tpl"),
        name="技能服务门客",
        rarity="green",
        archetype="military",
    )
    payload = {
        "manor": manor,
        "template": template,
        "level": 10,
        "force": 100,
        "intellect": 90,
        "defense_stat": 80,
        "agility": 85,
        "status": GuestStatus.IDLE,
    }
    payload.update(overrides)
    return Guest.objects.create(**payload)


def _create_skill_book_item(manor, **skill_overrides) -> tuple[Skill, InventoryItem]:
    payload = {
        "key": _unique("skill_service_skill"),
        "name": "技能服务技能",
        "rarity": "green",
    }
    payload.update(skill_overrides)
    skill = Skill.objects.create(**payload)
    template = ItemTemplate.objects.create(
        key=_unique("skill_service_book"),
        name="技能服务技能书",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": skill.key},
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    return skill, item


@pytest.mark.django_db
def test_learn_guest_skill_creates_guest_skill_and_consumes_book(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    skill, item = _create_skill_book_item(manor)

    learned = learn_guest_skill(guest, skill, item)

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 1
    assert learned.guest_id == guest.id
    assert learned.skill_id == skill.id
    assert GuestSkill.objects.filter(guest=guest, skill=skill, source=GuestSkill.Source.BOOK).exists()


@pytest.mark.django_db
def test_learn_guest_skill_rejects_book_from_another_manor(django_user_model):
    owner = django_user_model.objects.create_user(username=_unique("skill_service_owner"), password="pass123")
    other = django_user_model.objects.create_user(username=_unique("skill_service_other"), password="pass123")
    owner_manor = ensure_manor(owner)
    other_manor = ensure_manor(other)
    guest = _create_guest(owner_manor)
    skill, item = _create_skill_book_item(other_manor)

    with pytest.raises(GuestItemOwnershipError, match="不属于您的庄园"):
        learn_guest_skill(guest, skill, item)

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()


@pytest.mark.django_db
def test_learn_guest_skill_rejects_mismatched_skill_identity(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_identity_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    book_skill, item = _create_skill_book_item(manor)
    other_skill = Skill.objects.create(
        key=_unique("skill_service_other_skill"),
        name="另一个技能",
    )

    with pytest.raises(GuestItemConfigurationError, match="技能书配置有误"):
        learn_guest_skill(guest, other_skill, item)

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert not GuestSkill.objects.filter(
        guest=guest,
        skill__in=(book_skill, other_skill),
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_learn_guest_skill_locked_requires_atomic_block(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_atomic_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    skill, item = _create_skill_book_item(manor)

    with pytest.raises(RuntimeError, match="transaction.atomic"):
        learn_guest_skill_locked(
            manor,
            guest,
            item.id,
            expected_skill_id=skill.id,
        )


@pytest.mark.django_db
def test_learn_guest_skill_locked_revalidates_warehouse_location(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_location_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    skill, item = _create_skill_book_item(manor)
    InventoryItem.objects.filter(pk=item.pk).update(storage_location=InventoryItem.StorageLocation.TREASURY)

    with pytest.raises(GuestItemOwnershipError, match="不属于您的庄园"):
        with transaction.atomic():
            locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
            locked_guest = Guest.objects.select_for_update().get(pk=guest.pk)
            learn_guest_skill_locked(
                locked_manor,
                locked_guest,
                item.id,
                expected_skill_id=skill.id,
            )

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()


@pytest.mark.django_db
def test_learn_guest_skill_rejects_busy_guest_without_consuming_book(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_busy_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    guest.status = GuestStatus.WORKING
    guest.save(update_fields=["status"])
    skill, item = _create_skill_book_item(manor)

    with pytest.raises(GuestNotIdleError, match="非空闲状态"):
        learn_guest_skill(guest, skill, item)

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()


@pytest.mark.django_db
def test_learn_guest_skill_rejects_low_level_without_consuming_book(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_level_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, level=9)
    skill, item = _create_skill_book_item(manor, required_level=12)

    with pytest.raises(GuestNotRequirementError, match="等级不足"):
        learn_guest_skill(guest, skill, item)

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()


@pytest.mark.django_db
def test_learn_guest_skill_rejects_low_attribute_without_consuming_book(
    django_user_model,
):
    user = django_user_model.objects.create_user(username=_unique("skill_service_attr_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, agility=84)
    skill, item = _create_skill_book_item(manor, required_agility=100)

    with pytest.raises(GuestNotRequirementError, match="敏捷不足"):
        learn_guest_skill(guest, skill, item)

    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert not GuestSkill.objects.filter(guest=guest, skill=skill).exists()


@pytest.mark.django_db
def test_learn_guest_skill_rejects_borrowed_gear_until_base_attribute_reaches_floor(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_gear_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, force=150)
    template = GearTemplate.objects.create(
        key=_unique("skill_service_force_gear"),
        name="借用武器",
        slot=GearSlot.WEAPON,
        extra_stats={"force": 100},
        set_key="skill_service_force_set",
        set_bonus={"pieces": 1, "bonus": {"force": 70}},
    )
    equip_guest(GearItem.objects.create(manor=manor, template=template), guest)
    guest.refresh_from_db()
    assert guest.force == 320
    skill, item = _create_skill_book_item(manor, rarity="purple", required_force=300)
    assert "非装备武力需 ≥ 210" in collect_skill_requirements(skill)
    assert collect_unmet_skill_requirements(guest, skill) == ["非装备武力需 ≥ 210（当前 150）"]

    with pytest.raises(GuestNotRequirementError, match="非装备武力不足"):
        learn_guest_skill(guest, skill, item)
    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2

    guest.force += 60
    guest.save(update_fields=["force"])
    learned = learn_guest_skill(guest, skill, item)
    item.refresh_from_db(fields=["quantity"])
    assert learned.skill_id == skill.id
    assert item.quantity == 1


@pytest.mark.django_db
def test_set_defense_counts_toward_total_but_not_non_equipment_skill_floor(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_set_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, defense_stat=120)
    template = GearTemplate.objects.create(
        key=_unique("skill_service_defense_gear"),
        name="借用护甲",
        slot=GearSlot.ARMOR,
        extra_stats={"defense": 61},
        set_key="skill_service_defense_set",
        set_bonus={"pieces": 1, "bonus": {"defense": 20}},
    )
    equip_guest(GearItem.objects.create(manor=manor, template=template), guest)
    guest.refresh_from_db()
    skill = Skill(rarity="blue", required_defense=201)

    assert guest.defense_stat == 201
    assert collect_non_equipment_skill_attributes(guest)["defense"] == 120
    assert "非装备防御需 ≥ 131" in collect_skill_requirements(skill)
    assert collect_unmet_skill_requirements(guest, skill) == ["非装备防御需 ≥ 131（当前 120）"]


@pytest.mark.django_db
def test_forget_guest_skill_deletes_skill_record(django_user_model):
    user = django_user_model.objects.create_user(username=_unique("skill_service_forget_user"), password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    skill = Skill.objects.create(key=_unique("skill_service_forget_skill"), name="遗忘服务技能", rarity="green")
    guest_skill = GuestSkill.objects.create(guest=guest, skill=skill)

    skill_name = forget_guest_skill(guest, guest_skill.id)

    assert skill_name == skill.name
    assert not GuestSkill.objects.filter(pk=guest_skill.pk).exists()


@pytest.mark.django_db
def test_forget_guest_skill_rejects_missing_guest(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("skill_service_missing_guest_user"), password="pass123"
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)
    guest_id = guest.pk
    guest.delete()

    with pytest.raises(GuestNotFoundError, match="门客不存在"):
        forget_guest_skill(guest, 999999)

    assert not Guest.objects.filter(pk=guest_id).exists()


@pytest.mark.django_db
def test_forget_guest_skill_rejects_missing_skill_record(django_user_model):
    user = django_user_model.objects.create_user(
        username=_unique("skill_service_missing_skill_user"), password="pass123"
    )
    manor = ensure_manor(user)
    guest = _create_guest(manor)

    with pytest.raises(GuestSkillNotFoundError, match="未找到要遗忘的技能"):
        forget_guest_skill(guest, 999999)
