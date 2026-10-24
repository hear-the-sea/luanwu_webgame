from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.exceptions import WorkNotInProgressError
from gameplay.models import ItemTemplate, Manor, TemporaryGuestOccupation, WorkAssignment, WorkTemplate
from gameplay.services import work
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate


def _make_due_work(django_user_model, *, count=5):
    manor = ensure_manor(django_user_model.objects.create_user(username="bounded_work_owner"))
    guest_template = GuestTemplate.objects.create(key="bounded_worker", name="有界扫描门客")
    now = timezone.now()
    assignments = []
    for index in range(count):
        guest = Guest.objects.create(manor=manor, template=guest_template, status=GuestStatus.WORKING)
        template = WorkTemplate.objects.create(
            key=f"bounded_work_{index}",
            name=f"工作{index}",
            required_level=1,
            required_force=0,
            required_intellect=0,
        )
        assignments.append(
            WorkAssignment.objects.create(
                manor=manor,
                guest=guest,
                work_template=template,
                complete_at=now - timedelta(minutes=1),
            )
        )
    return manor, assignments


@pytest.mark.django_db(transaction=True)
def test_global_work_scan_bounds_candidates_and_commits_each_batch(settings, monkeypatch, django_user_model):
    settings.WORK_COMPLETION_BATCH_SIZE = 2
    settings.WORK_COMPLETION_SCAN_LIMIT = 3
    _manor, assignments = _make_due_work(django_user_model)
    commits = []
    batch_counts = []
    original_complete = work._complete_assignment_batch

    def observe_commit(assignment_ids, now, *, locked_guests):
        batch_counts.append(len(assignment_ids))
        transaction.on_commit(lambda: commits.append(tuple(assignment_ids)))
        return original_complete(assignment_ids, now, locked_guests=locked_guests)

    monkeypatch.setattr(work, "_complete_assignment_batch", observe_commit)
    with CaptureQueriesContext(connection) as captured:
        assert work.complete_work_assignments() == 3

    assert batch_counts == [2, 1]
    assert len(commits) == 2
    assert not connection.in_atomic_block
    assert list(WorkAssignment.objects.order_by("pk").values_list("status", flat=True)) == [
        WorkAssignment.Status.COMPLETED,
        WorkAssignment.Status.COMPLETED,
        WorkAssignment.Status.COMPLETED,
        WorkAssignment.Status.WORKING,
        WorkAssignment.Status.WORKING,
    ]
    table = WorkAssignment._meta.db_table
    normalized_queries = [query["sql"].replace('"', "").replace("`", "") for query in captured]
    candidate_queries = [
        query
        for query in normalized_queries
        if query.startswith("SELECT ")
        and all(f"{table}.{field}" in query.split(" FROM ", 1)[0] for field in ("manor_id", "guest_id", "complete_at"))
    ]
    assert len(candidate_queries) == 2
    assert all(" LIMIT " in query for query in candidate_queries)
    assert work.complete_work_assignments() == 2
    assert work.complete_work_assignments() == 0
    assert Guest.objects.filter(pk__in=[row.guest_id for row in assignments], status=GuestStatus.IDLE).count() == 5


@pytest.mark.django_db
def test_global_scan_attempt_limit_applies_when_all_manors_are_busy(settings, monkeypatch, django_user_model):
    settings.WORK_COMPLETION_BATCH_SIZE = 2
    settings.WORK_COMPLETION_SCAN_LIMIT = 3
    _make_due_work(django_user_model)
    observed_ids = []

    def busy_batch(candidates, now, *, skip_locked):
        assert skip_locked
        observed_ids.extend(row[0] for row in candidates)
        return 0

    monkeypatch.setattr(work, "_complete_locked_work_batch", busy_batch)
    assert work.complete_work_assignments() == 0
    assert len(observed_ids) == len(set(observed_ids)) == 3


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["global", "refresh", "recall", "claim"])
def test_work_writes_lock_manor_before_guest_and_assignment(monkeypatch, django_user_model, operation):
    manor, assignments = _make_due_work(django_user_model, count=1)
    assignment = assignments[0]
    if operation == "claim":
        WorkAssignment.objects.filter(pk=assignment.pk).update(status=WorkAssignment.Status.COMPLETED)
        ItemTemplate.objects.create(key="work_chest_small", name="打工宝箱")

    lock_order = []
    for label, model in (("manor", Manor), ("guest", Guest), ("assignment", WorkAssignment)):
        original = model.objects.select_for_update

        def record_lock(*args, _label=label, _original=original, **kwargs):
            lock_order.append(_label)
            return _original(*args, **kwargs)

        monkeypatch.setattr(model.objects, "select_for_update", record_lock)

    if operation == "global":
        assert work.complete_work_assignments() == 1
    elif operation == "refresh":
        assert work.refresh_work_assignments(manor) == 1
    elif operation == "recall":
        assert work.recall_guest_from_work(assignment)
    else:
        work.claim_work_reward(assignment)
    assert lock_order[:3] == ["manor", "guest", "assignment"]


@pytest.mark.django_db
@pytest.mark.parametrize("has_active_work", [False, True])
def test_global_scan_releases_expired_temporary_occupation_without_overriding_work(django_user_model, has_active_work):
    manor, assignments = _make_due_work(django_user_model, count=1)
    assignment = assignments[0]
    if has_active_work:
        WorkAssignment.objects.filter(pk=assignment.pk).update(complete_at=timezone.now() + timedelta(hours=1))
    else:
        WorkAssignment.objects.filter(pk=assignment.pk).delete()
    occupation = TemporaryGuestOccupation.objects.create(
        manor=manor,
        guest_id=assignment.guest_id,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    assert work.complete_work_assignments() == 0

    assert not TemporaryGuestOccupation.objects.filter(pk=occupation.pk).exists()
    guest = Guest.objects.get(pk=assignment.guest_id)
    assert guest.status == (GuestStatus.WORKING if has_active_work else GuestStatus.IDLE)


@pytest.mark.django_db
def test_zero_work_scan_limit_does_not_query_database(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert work.complete_work_assignments(limit=0) == 0


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_concurrent_work_scanners_complete_each_assignment_once(settings, django_user_model):
    if not connection.features.has_select_for_update_skip_locked:
        pytest.skip("requires row locks with SKIP LOCKED")
    settings.WORK_COMPLETION_BATCH_SIZE = 2
    settings.WORK_COMPLETION_SCAN_LIMIT = 5
    _manor, assignments = _make_due_work(django_user_model)
    barrier = threading.Barrier(2)

    def scan():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return work.complete_work_assignments()
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(scan) for _ in range(2)]
        counts = [future.result(timeout=30) for future in futures]

    assert sum(counts) == len(assignments)
    assert WorkAssignment.objects.filter(status=WorkAssignment.Status.COMPLETED).count() == len(assignments)
    assert Guest.objects.filter(status=GuestStatus.IDLE).count() == len(assignments)


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_work_scan_skips_busy_manor_without_blocking_other_manors(settings, django_user_model):
    if not connection.features.has_select_for_update_skip_locked:
        pytest.skip("requires row locks with SKIP LOCKED")
    settings.WORK_COMPLETION_BATCH_SIZE = 2
    settings.WORK_COMPLETION_SCAN_LIMIT = 10
    busy_manor, busy_assignments = _make_due_work(django_user_model, count=2)
    available_manor = ensure_manor(django_user_model.objects.create_user(username="available_work_owner"))
    guest = Guest.objects.create(
        manor=available_manor, template=busy_assignments[0].guest.template, status=GuestStatus.WORKING
    )
    available_assignment = WorkAssignment.objects.create(
        manor=available_manor,
        guest=guest,
        work_template=busy_assignments[0].work_template,
        complete_at=timezone.now() - timedelta(seconds=1),
    )
    locked = threading.Event()
    release = threading.Event()

    def hold_manor_lock():
        close_old_connections()
        try:
            with transaction.atomic():
                Manor.objects.select_for_update().get(pk=busy_manor.pk)
                locked.set()
                assert release.wait(timeout=20)
        finally:
            connections["default"].close()

    def scan():
        close_old_connections()
        try:
            return work.complete_work_assignments()
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_manor_lock)
        try:
            assert locked.wait(timeout=10)
            assert executor.submit(scan).result(timeout=10) == 1
        finally:
            release.set()
        holder.result(timeout=10)

    assert WorkAssignment.objects.get(pk=available_assignment.pk).status == WorkAssignment.Status.COMPLETED
    assert WorkAssignment.objects.filter(pk__in=[row.pk for row in busy_assignments], status="working").count() == 2
    assert work.complete_work_assignments() == 2


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_work_scanner_racing_recall_and_reassignment_preserves_new_work(django_user_model):
    if not connection.features.has_select_for_update_skip_locked:
        pytest.skip("requires row locks with SKIP LOCKED")
    manor, assignments = _make_due_work(django_user_model, count=1)
    old_assignment = assignments[0]
    next_template = WorkTemplate.objects.create(
        key="bounded_reassigned_work",
        name="新工作",
        work_duration=3600,
        required_level=1,
        required_force=0,
        required_intellect=0,
    )
    barrier = threading.Barrier(2)

    def scan():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return work.complete_work_assignments()
        finally:
            connections["default"].close()

    def reassign():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            assignment = WorkAssignment.objects.get(pk=old_assignment.pk)
            try:
                work.recall_guest_from_work(assignment)
            except WorkNotInProgressError:
                pass
            guest = Guest.objects.get(pk=old_assignment.guest_id)
            return work.assign_guest_to_work(guest, next_template).pk
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        scan_future = executor.submit(scan)
        reassign_future = executor.submit(reassign)
        scan_future.result(timeout=30)
        next_assignment_id = reassign_future.result(timeout=30)

    assert WorkAssignment.objects.get(pk=old_assignment.pk).status in {
        WorkAssignment.Status.RECALLED,
        WorkAssignment.Status.COMPLETED,
    }
    assert WorkAssignment.objects.get(pk=next_assignment_id).status == WorkAssignment.Status.WORKING
    assert Guest.objects.get(pk=old_assignment.guest_id, manor=manor).status == GuestStatus.WORKING
