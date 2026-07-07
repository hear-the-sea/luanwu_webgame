from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

import gameplay.services.work as work_service
from core.exceptions import (
    ActionPointsInsufficientError,
    WorkError,
    WorkLimitExceededError,
    WorkNotInProgressError,
    WorkRewardClaimedError,
)
from gameplay.models import InventoryItem, ItemTemplate, WorkAssignment, WorkTemplate
from gameplay.services.action_points import ACTION_POINT_EXPEDITION_COST
from gameplay.services.manor.core import ensure_manor
from gameplay.services.work import assign_guest_to_work, claim_work_reward, recall_guest_from_work
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate


def _create_work_chest_templates() -> None:
    for key, name in (
        ("work_chest_small", "打工宝箱（小）"),
        ("work_chest_medium", "打工宝箱（中）"),
        ("work_chest_large", "打工宝箱（大）"),
    ):
        ItemTemplate.objects.create(
            key=key,
            name=name,
            effect_type=ItemTemplate.EffectType.LOOT_BOX,
        )


@pytest.mark.django_db
def test_claim_work_reward_rechecks_locked_assignment_state(django_user_model):
    user = django_user_model.objects.create_user(username="work_claim_lock_user", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 0
    manor.save(update_fields=["silver"])
    _create_work_chest_templates()

    guest_template = GuestTemplate.objects.create(
        key=f"work_claim_lock_tpl_{user.id}",
        name="并发领取模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    work_template = WorkTemplate.objects.create(
        key=f"work_claim_lock_work_{user.id}",
        name="并发领取工作",
        reward_silver=123,
        work_duration=60,
    )
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        status=WorkAssignment.Status.COMPLETED,
        complete_at=timezone.now(),
    )

    stale_a = WorkAssignment.objects.get(pk=assignment.pk)
    stale_b = WorkAssignment.objects.get(pk=assignment.pk)

    result = claim_work_reward(stale_a)
    assert result["silver"] == 123

    with pytest.raises(WorkRewardClaimedError):
        claim_work_reward(stale_b)

    manor.refresh_from_db()
    assignment.refresh_from_db()
    assert manor.silver == 123
    assert assignment.reward_claimed is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("tier", "expected_chest_key"),
    (
        (WorkTemplate.Tier.JUNIOR, "work_chest_small"),
        (WorkTemplate.Tier.INTERMEDIATE, "work_chest_medium"),
        (WorkTemplate.Tier.SENIOR, "work_chest_large"),
    ),
)
def test_claim_work_reward_grants_tier_work_chest(django_user_model, tier, expected_chest_key):
    user = django_user_model.objects.create_user(username=f"work_chest_{tier}", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 0
    manor.save(update_fields=["silver"])
    _create_work_chest_templates()

    guest_template = GuestTemplate.objects.create(
        key=f"work_chest_tpl_{tier}_{user.id}",
        name="宝箱领取模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    work_template = WorkTemplate.objects.create(
        key=f"work_chest_work_{tier}_{user.id}",
        name="宝箱领取工作",
        tier=tier,
        reward_silver=321,
        work_duration=60,
    )
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        status=WorkAssignment.Status.COMPLETED,
        complete_at=timezone.now(),
    )

    reward = claim_work_reward(assignment)

    manor.refresh_from_db()
    assert manor.silver == 321
    assert reward["silver"] == 321
    assert reward["item_key"] == expected_chest_key
    assert (
        InventoryItem.objects.filter(
            manor=manor,
            template__key=expected_chest_key,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=1,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_recall_guest_from_work_rechecks_locked_assignment_state(django_user_model):
    user = django_user_model.objects.create_user(username="work_recall_lock_user", password="pass123")
    manor = ensure_manor(user)

    guest_template = GuestTemplate.objects.create(
        key=f"work_recall_lock_tpl_{user.id}",
        name="并发召回模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.WORKING)
    work_template = WorkTemplate.objects.create(
        key=f"work_recall_lock_work_{user.id}",
        name="并发召回工作",
        reward_silver=88,
        work_duration=60,
    )
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        status=WorkAssignment.Status.WORKING,
        complete_at=timezone.now(),
    )

    stale_assignment = WorkAssignment.objects.get(pk=assignment.pk)
    WorkAssignment.objects.filter(pk=assignment.pk).update(
        status=WorkAssignment.Status.COMPLETED,
        finished_at=timezone.now(),
    )
    Guest.objects.filter(pk=guest.pk).update(status=GuestStatus.IDLE)

    with pytest.raises(WorkNotInProgressError):
        recall_guest_from_work(stale_assignment)

    assignment.refresh_from_db()
    guest.refresh_from_db()
    assert assignment.status == WorkAssignment.Status.COMPLETED
    assert guest.status == GuestStatus.IDLE


def _patch_recall_and_reassign_before_bulk_completion(monkeypatch, *, assignment, guest, new_work_template):
    original_filter = work_service.WorkAssignment.objects.filter
    triggered = {"value": False}
    new_assignment_pk: dict[str, int] = {}

    def _patched_filter(*args, **kwargs):
        target_ids = kwargs.get("id__in")
        if (
            not triggered["value"]
            and kwargs.get("status") == WorkAssignment.Status.WORKING
            and target_ids is not None
            and set(target_ids) == {assignment.pk}
        ):
            triggered["value"] = True
            original_filter(pk=assignment.pk).update(
                status=WorkAssignment.Status.RECALLED,
                finished_at=timezone.now(),
            )
            Guest.objects.filter(pk=guest.pk).update(status=GuestStatus.IDLE)
            new_assignment = WorkAssignment.objects.create(
                manor=assignment.manor,
                guest=guest,
                work_template=new_work_template,
                status=WorkAssignment.Status.WORKING,
                complete_at=timezone.now() + timedelta(minutes=30),
            )
            Guest.objects.filter(pk=guest.pk).update(status=GuestStatus.WORKING)
            new_assignment_pk["value"] = new_assignment.pk
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(work_service.WorkAssignment.objects, "filter", _patched_filter)
    return triggered, new_assignment_pk


@pytest.mark.django_db
def test_complete_work_assignments_does_not_idle_reassigned_guest(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="work_complete_reassign_user", password="pass123")
    manor = ensure_manor(user)

    guest_template = GuestTemplate.objects.create(
        key=f"work_complete_reassign_tpl_{user.id}",
        name="自动完成并发模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.WORKING)
    expired_work_template = WorkTemplate.objects.create(
        key=f"work_complete_expired_{user.id}",
        name="已到期旧工作",
        reward_silver=50,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )
    next_work_template = WorkTemplate.objects.create(
        key=f"work_complete_next_{user.id}",
        name="重新派遣新工作",
        reward_silver=60,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=expired_work_template,
        status=WorkAssignment.Status.WORKING,
        complete_at=timezone.now() - timedelta(minutes=1),
    )

    triggered, new_assignment_pk = _patch_recall_and_reassign_before_bulk_completion(
        monkeypatch,
        assignment=assignment,
        guest=guest,
        new_work_template=next_work_template,
    )

    updated_count = work_service.complete_work_assignments()

    assignment.refresh_from_db()
    guest.refresh_from_db()
    new_assignment = WorkAssignment.objects.get(pk=new_assignment_pk["value"])

    assert triggered["value"] is True
    assert updated_count == 0
    assert assignment.status == WorkAssignment.Status.RECALLED
    assert new_assignment.status == WorkAssignment.Status.WORKING
    assert guest.status == GuestStatus.WORKING


@pytest.mark.django_db
def test_refresh_work_assignments_does_not_idle_reassigned_guest(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="work_refresh_reassign_user", password="pass123")
    manor = ensure_manor(user)

    guest_template = GuestTemplate.objects.create(
        key=f"work_refresh_reassign_tpl_{user.id}",
        name="刷新并发模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.WORKING)
    expired_work_template = WorkTemplate.objects.create(
        key=f"work_refresh_expired_{user.id}",
        name="刷新旧工作",
        reward_silver=50,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )
    next_work_template = WorkTemplate.objects.create(
        key=f"work_refresh_next_{user.id}",
        name="刷新新工作",
        reward_silver=60,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=expired_work_template,
        status=WorkAssignment.Status.WORKING,
        complete_at=timezone.now() - timedelta(minutes=1),
    )

    triggered, new_assignment_pk = _patch_recall_and_reassign_before_bulk_completion(
        monkeypatch,
        assignment=assignment,
        guest=guest,
        new_work_template=next_work_template,
    )

    work_service.refresh_work_assignments(manor)

    assignment.refresh_from_db()
    guest.refresh_from_db()
    new_assignment = WorkAssignment.objects.get(pk=new_assignment_pk["value"])

    assert triggered["value"] is True
    assert assignment.status == WorkAssignment.Status.RECALLED
    assert new_assignment.status == WorkAssignment.Status.WORKING
    assert guest.status == GuestStatus.WORKING


@pytest.mark.django_db
def test_assign_guest_to_work_rejects_when_same_work_template_is_busy(django_user_model):
    user = django_user_model.objects.create_user(username="work_same_template_user", password="pass123")
    manor = ensure_manor(user)

    guest_template = GuestTemplate.objects.create(
        key=f"work_same_tpl_{user.id}",
        name="同工位测试模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest1 = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    guest2 = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    work_template = WorkTemplate.objects.create(
        key=f"work_same_template_{user.id}",
        name="同工位测试工作",
        reward_silver=80,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )

    assignment = assign_guest_to_work(guest1, work_template)
    assert assignment.status == WorkAssignment.Status.WORKING

    with pytest.raises(WorkError, match="当前已有门客在打工"):
        assign_guest_to_work(guest2, work_template)


@pytest.mark.django_db
def test_assign_guest_to_work_consumes_action_points(django_user_model):
    user = django_user_model.objects.create_user(username="work_action_points_user", password="pass123")
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key=f"work_action_points_tpl_{user.id}",
        name="行动力打工模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    work_template = WorkTemplate.objects.create(
        key=f"work_action_points_work_{user.id}",
        name="行动力打工",
        reward_silver=80,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )

    assignment = assign_guest_to_work(guest, work_template)

    manor.refresh_from_db()
    assert assignment.status == WorkAssignment.Status.WORKING
    assert manor.action_points == 1000 - ACTION_POINT_EXPEDITION_COST


@pytest.mark.django_db
def test_assign_guest_to_work_rejects_when_action_points_insufficient(django_user_model):
    user = django_user_model.objects.create_user(username="work_no_action_points_user", password="pass123")
    manor = ensure_manor(user)
    manor.action_points = ACTION_POINT_EXPEDITION_COST - 1
    manor.save(update_fields=["action_points"])
    guest_template = GuestTemplate.objects.create(
        key=f"work_no_action_points_tpl_{user.id}",
        name="行动力不足打工模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    work_template = WorkTemplate.objects.create(
        key=f"work_no_action_points_work_{user.id}",
        name="行动力不足打工",
        reward_silver=80,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )

    with pytest.raises(ActionPointsInsufficientError, match="行动力不足"):
        assign_guest_to_work(guest, work_template)

    manor.refresh_from_db()
    guest.refresh_from_db()
    assert manor.action_points == ACTION_POINT_EXPEDITION_COST - 1
    assert guest.status == GuestStatus.IDLE
    assert WorkAssignment.objects.filter(manor=manor).exists() is False


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_assign_guest_to_work_concurrent_requests_respect_limit_inside_lock(monkeypatch, django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    user = django_user_model.objects.create_user(username="work_concurrent_user", password="pass123")
    manor = ensure_manor(user)

    guest_template = GuestTemplate.objects.create(
        key=f"work_concurrent_tpl_{user.id}",
        name="并发打工模板",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest1 = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    guest2 = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.IDLE)
    work_template_1 = WorkTemplate.objects.create(
        key=f"work_concurrent_work_{user.id}",
        name="并发打工工作A",
        reward_silver=50,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )
    work_template_2 = WorkTemplate.objects.create(
        key=f"work_concurrent_work_b_{user.id}",
        name="并发打工工作B",
        reward_silver=50,
        work_duration=60,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )

    monkeypatch.setattr("gameplay.services.work.MAX_CONCURRENT_WORKERS", 1)

    barrier = threading.Barrier(2)
    results: list[int] = []
    errors: list[Exception] = []

    def _worker(guest_id: int, work_template_id: int):
        try:
            local_guest = Guest.objects.get(pk=guest_id)
            local_work_template = WorkTemplate.objects.get(pk=work_template_id)
            barrier.wait(timeout=5)
            assignment = assign_guest_to_work(local_guest, local_work_template)
            results.append(assignment.pk)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(guest1.pk, work_template_1.pk)),
        threading.Thread(target=_worker, args=(guest2.pk, work_template_2.pk)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], WorkLimitExceededError)
    assert WorkAssignment.objects.filter(manor=manor, status=WorkAssignment.Status.WORKING).count() == 1
