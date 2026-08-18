"""
测试 guests.services 模块重构后的功能
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from core.exceptions import InvalidAllocationError
from gameplay.services.manor.core import ensure_manor
from guests.constants import TimeConstants
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.rarity import GUEST_RARITY_ORDER
from guests.services import health as health_service
from guests.services.health import recover_guest_hp
from guests.services.recruitment_guests import allocate_attribute_points
from guests.services.recruitment_queries import available_guests, list_pools
from guests.tasks import scan_passive_hp_recovery

User = get_user_model()


def test_guest_rarity_order_matches_model_choices():
    assert tuple(value for value, _label in GuestRarity.choices) == GUEST_RARITY_ORDER


@pytest.mark.django_db
def test_list_pools():
    """测试获取招募卡池列表"""
    pools = list(list_pools())
    # 应该返回可迭代对象（即使为空）
    assert isinstance(pools, list)


@pytest.mark.django_db
def test_available_guests_empty():
    """测试获取可用门客（空）"""
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)

    guests = list(available_guests(manor))
    assert guests == []


@pytest.mark.django_db
def test_available_guests_ordered():
    """测试获取可用门客（按稀有度和等级排序）"""
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)

    # 创建测试门客模板
    template1 = GuestTemplate.objects.create(
        key="test_guest_1",
        name="测试门客1",
        rarity=GuestRarity.GRAY,
        archetype=GuestArchetype.MILITARY,
        base_attack=50,
        base_defense=50,
    )
    template2 = GuestTemplate.objects.create(
        key="test_guest_2",
        name="测试门客2",
        rarity=GuestRarity.RED,
        archetype=GuestArchetype.MILITARY,
        base_attack=80,
        base_defense=80,
    )
    template3 = GuestTemplate.objects.create(
        key="test_guest_3",
        name="测试门客3",
        rarity=GuestRarity.BLUE,
        archetype=GuestArchetype.MILITARY,
        base_attack=70,
        base_defense=70,
    )

    # 创建门客
    guest1 = Guest.objects.create(manor=manor, template=template1, force=50, intellect=50, level=1)
    guest2 = Guest.objects.create(manor=manor, template=template2, force=80, intellect=80, level=99)
    guest3 = Guest.objects.create(manor=manor, template=template3, force=70, intellect=70, level=1)

    guests = list(available_guests(manor))

    # 应该返回三个门客，且按业务稀有度排序（蓝 > 红 > 灰）
    assert len(guests) == 3
    assert [g.id for g in guests] == [guest3.id, guest2.id, guest1.id]


@pytest.mark.django_db
def test_recover_guest_hp_already_full():
    """测试恢复生命值（已满血）"""
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest", name="测试门客", rarity="gray", base_attack=50, base_defense=50
    )
    guest = Guest.objects.create(manor=manor, template=template, force=50, intellect=50)

    initial_hp = guest.current_hp
    recover_guest_hp(guest)
    guest.refresh_from_db()

    # 生命值不应该超过最大值
    assert guest.current_hp == initial_hp


@pytest.mark.django_db
def test_recover_guest_hp_injured_is_one_tenth_of_normal_speed():
    user = User.objects.create_user(username="testuser_hp_recover_ratio", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest_recover_ratio",
        name="测试门客恢复比例",
        rarity="gray",
        base_attack=50,
        base_defense=50,
    )
    now = timezone.now()
    last = now - timezone.timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)

    idle_guest = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.IDLE,
        current_hp=1,
        last_hp_recovery_at=last,
    )
    injured_guest = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.INJURED,
        current_hp=1,
        last_hp_recovery_at=last,
    )

    with override_settings(GAME_TIME_MULTIPLIER=1):
        recover_guest_hp(idle_guest, now=now)
        recover_guest_hp(injured_guest, now=now)
    idle_guest.refresh_from_db()
    injured_guest.refresh_from_db()

    idle_gain = idle_guest.current_hp - 1
    injured_gain = injured_guest.current_hp - 1
    assert injured_gain > 0
    assert idle_gain == injured_gain * 10


@pytest.mark.django_db
def test_recover_guest_hp_injured_respects_global_time_multiplier():
    user = User.objects.create_user(username="testuser_hp_recover_scale", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest_recover_scale",
        name="测试门客恢复倍率",
        rarity="gray",
        base_attack=50,
        base_defense=50,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.INJURED,
        current_hp=1,
    )
    now = timezone.now()
    last = now - timezone.timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)

    with override_settings(GAME_TIME_MULTIPLIER=1):
        guest.current_hp = 1
        guest.last_hp_recovery_at = last
        guest.save(update_fields=["current_hp", "last_hp_recovery_at"])
        recover_guest_hp(guest, now=now)
        guest.refresh_from_db()
        base_gain = guest.current_hp - 1

    with override_settings(GAME_TIME_MULTIPLIER=5):
        guest.current_hp = 1
        guest.last_hp_recovery_at = last
        guest.save(update_fields=["current_hp", "last_hp_recovery_at"])
        recover_guest_hp(guest, now=now)
        guest.refresh_from_db()
        scaled_gain = guest.current_hp - 1

    assert base_gain > 0
    assert scaled_gain == base_gain * 5


@pytest.mark.django_db
def test_recover_guest_hp_clears_injured_status_at_recovery_threshold():
    user = User.objects.create_user(username="testuser_hp_recover_threshold_injured", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest_recover_threshold_injured",
        name="测试门客达到阈值解除重伤",
        rarity="gray",
        base_attack=50,
        base_defense=50,
        base_hp=1000,
    )
    now = timezone.now()

    guest = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.INJURED,
        current_hp=1,
        last_hp_recovery_at=now,
    )

    with override_settings(GAME_TIME_MULTIPLIER=1):
        per_second = max(1, (guest.max_hp - 1) / TimeConstants.HP_FULL_RECOVERY_TIME)
        recovery_seconds = (
            int(
                guest.max_hp
                * 0.25
                / (per_second * health_service.INJURED_RECOVERY_RATE_FACTOR * manor.hp_recovery_multiplier)
            )
            + TimeConstants.HP_RECOVERY_INTERVAL
        )
        guest.last_hp_recovery_at = now - timezone.timedelta(seconds=recovery_seconds)
        guest.save(update_fields=["last_hp_recovery_at"])
        recover_guest_hp(guest, now=now)
    guest.refresh_from_db()

    assert guest.current_hp >= guest.max_hp * 0.2
    assert guest.current_hp < guest.max_hp
    assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_scan_passive_hp_recovery_clears_full_hp_injured_status():
    user = User.objects.create_user(username="testuser_hp_scan_full_injured", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest_scan_full_injured",
        name="测试门客扫描满血解除重伤",
        rarity="gray",
        base_attack=50,
        base_defense=50,
    )
    now = timezone.now()
    last = now - timezone.timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.INJURED,
        current_hp=max(1, template.base_hp + 50 * 80),
        last_hp_recovery_at=last,
    )

    processed = scan_passive_hp_recovery(limit=10)
    guest.refresh_from_db()

    assert processed == 1
    assert guest.current_hp == guest.max_hp
    assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_scan_passive_hp_recovery_passes_ordered_ids_and_counts_changed_guests(
    monkeypatch,
):
    user = User.objects.create_user(username="testuser_hp_scan_ids", password="test123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="test_guest_scan_ids",
        name="测试门客扫描ID",
        rarity=GuestRarity.GRAY,
        base_attack=50,
        base_defense=50,
    )
    now = timezone.now()
    last = now - timezone.timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)
    first_guest = Guest.objects.create(
        manor=manor,
        template=template,
        current_hp=1,
        last_hp_recovery_at=last,
    )
    second_guest = Guest.objects.create(
        manor=manor,
        template=template,
        current_hp=1,
        last_hp_recovery_at=last,
    )
    seen_guest_ids: list[int] = []

    def _recover_by_id(guest_id: int, *, now) -> bool:
        assert isinstance(guest_id, int)
        seen_guest_ids.append(guest_id)
        return guest_id == first_guest.id

    monkeypatch.setattr(health_service, "recover_guest_hp_for_guest", _recover_by_id)
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)

    processed = scan_passive_hp_recovery(limit=10)

    assert processed == 1
    assert seen_guest_ids == [first_guest.id, second_guest.id]


@pytest.mark.django_db
def test_allocate_attribute_points():
    """测试分配属性点"""
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest", name="测试门客", rarity="gray", base_attack=50, base_defense=50
    )
    guest = Guest.objects.create(manor=manor, template=template, force=50, intellect=50, attribute_points=10)

    initial_force = guest.force
    allocate_attribute_points(guest, "force", 5)
    guest.refresh_from_db()

    assert guest.attribute_points == 5
    assert guest.force == initial_force + 5


@pytest.mark.django_db
def test_allocate_attribute_points_insufficient():
    """测试分配属性点（属性点不足）"""
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="test_guest", name="测试门客", rarity="gray", base_attack=50, base_defense=50
    )
    guest = Guest.objects.create(manor=manor, template=template, force=50, intellect=50, attribute_points=3)

    with pytest.raises(InvalidAllocationError, match="属性点不足"):
        allocate_attribute_points(guest, "force", 5)
