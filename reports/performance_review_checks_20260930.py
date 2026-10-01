from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection, transaction
from django.utils import timezone

from guests.models import Guest, GuestStatus
from guests.services import health
from tests.test_bulk_guest_healing import _guest_template, _medicine
from tests.test_bulk_guest_healing_query_reuse import _freeze_healing_clock, _snapshot
from tests.test_resource_grain_reuse import _baseline_service
from tests.test_resource_grain_reuse import baseline_modules as _resource_baseline_fixture

pytest_plugins = ["tests.conftest"]


@pytest.fixture(scope="module")
def historical_resource_baseline():
    return _resource_baseline_fixture.__wrapped__()


@pytest.fixture(scope="module")
def legacy_health():
    old_source = subprocess.check_output(
        ["git", "show", "70165a0c9cc5954f4574063b6b8bf0beca287d85:guests/services/health.py"], text=True
    )
    old_tree = ast.parse(old_source)
    new_tree = ast.parse(Path(health.__file__).read_text())
    old_apply = next(
        n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == "apply_medicine_item_for_guest_locked"
    )
    new_apply = next(
        n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_medicine_to_locked_objects"
    )
    quote_index = next(
        i
        for i, n in enumerate(old_apply.body)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "quote" for t in n.targets)
    )
    assert [ast.dump(n) for n in old_apply.body[quote_index:]] == [ast.dump(n) for n in new_apply.body[2:]]
    old_path = Path("/tmp/webgame_review_legacy_health.py")
    old_path.write_text(old_source)
    name = "guests.services._review_legacy_health"
    spec = importlib.util.spec_from_file_location(name, old_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.django_db
@pytest.mark.parametrize("quantity", [1, 3, 8])
def test_complete_old_healing_module_matches_optimized_results(manor_with_user, monkeypatch, legacy_health, quantity):
    manor, _client = manor_with_user
    now = timezone.now()
    _freeze_healing_clock(monkeypatch, now)
    template = _guest_template()
    for status, hp in ((GuestStatus.IDLE, 900), (GuestStatus.INJURED, 1), (GuestStatus.IDLE, 600)):
        Guest.objects.create(
            manor=manor,
            template=template,
            defense_stat=0,
            status=status,
            current_hp=hp,
            loyalty=80,
            injury_loyalty_processed_at=now - timedelta(hours=6),
            training_remaining_seconds=90 if status == GuestStatus.INJURED else None,
        )
    _medicine(manor, heal_amount=150, quantity=quantity)
    _medicine(manor, heal_amount=500, quantity=quantity)
    queued = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda guest, *, countdown, source: queued.append((guest.pk, countdown, source, guest.training_complete_at)),
    )
    with transaction.atomic():
        callback_start = len(connection.run_on_commit)
        expected = legacy_health.heal_all_guests_with_medicine(manor)
        expected_rows = _snapshot(manor)
        for _, callback, _ in connection.run_on_commit[callback_start:]:
            callback()
        expected_callbacks = list(queued)
        transaction.set_rollback(True)
    queued.clear()
    callback_start = len(connection.run_on_commit)
    actual = health.heal_all_guests_with_medicine(manor)
    for _, callback, _ in connection.run_on_commit[callback_start:]:
        callback()
    assert actual == expected
    assert _snapshot(manor) == expected_rows
    assert queued == expected_callbacks


@pytest.mark.django_db
@pytest.mark.parametrize("manor_count", [1, 50])
def test_resource_candidate_query_profile(monkeypatch, manor_count, historical_resource_baseline):
    from django.contrib.auth import get_user_model
    from django.test.utils import CaptureQueriesContext

    from gameplay.models import InventoryItem, Manor, ResourceEvent, ResourceType
    from gameplay.services import resources
    from tests.gameplay_services.support import ensure_grain_template

    now = timezone.now()
    User = get_user_model()
    names = [f"resource-review-{index}" for index in range(manor_count)]
    User.objects.bulk_create([User(username=name) for name in names])
    users = list(User.objects.filter(username__in=names).order_by("id"))
    Manor.objects.bulk_create(
        [Manor(user=user, grain=100, resource_updated_at=now - timedelta(hours=1)) for user in users]
    )
    manors = list(Manor.objects.filter(user__in=users))
    grain = ensure_grain_template()
    InventoryItem.objects.bulk_create([InventoryItem(manor=manor, template=grain, quantity=100) for manor in manors])
    basis = resources.ResourceProductionBasis(
        hourly_rates=((ResourceType.GRAIN, 120.0),), personnel_grain_cost_per_hour=0
    )
    # Keep this historical hotspot probe reproducible after the candidate is optimized.
    monkeypatch.setattr(
        historical_resource_baseline.resources,
        "load_resource_production_bases",
        lambda rows: {int(row.pk): basis for row in rows},
    )
    with (
        _baseline_service(historical_resource_baseline) as legacy_resources,
        CaptureQueriesContext(connection) as queries,
    ):
        count = legacy_resources.sync_resource_production_batch(
            [manor.pk for manor in manors], grain_template=grain, grain_template_resolved=True, now=now
        )
    assert count == manor_count
    inventory_selects = sum(
        q["sql"].lstrip().upper().startswith("SELECT") and "gameplay_inventoryitem" in q["sql"] for q in queries
    )
    event_inserts = sum(
        q["sql"].lstrip().upper().startswith("INSERT") and "gameplay_resourceevent" in q["sql"] for q in queries
    )
    assert inventory_selects == 2 * manor_count
    assert event_inserts == manor_count
    assert ResourceEvent.objects.filter(manor__in=manors).count() == manor_count
    assert set(InventoryItem.objects.filter(manor__in=manors).values_list("quantity", flat=True)) == {220}
    print(
        f"resource_candidate_profile vendor={connection.vendor} manors={manor_count} sql={len(queries)} "
        f"inventory_selects={inventory_selects} event_inserts={event_inserts}"
    )


@pytest.mark.django_db
def test_arena_skill_candidate_query_profile(manor_with_user):
    from django.test.utils import CaptureQueriesContext

    from gameplay.services.arena.snapshots import serialize_guest_skill_keys
    from guests.models import GuestSkill, Skill

    manor, _client = manor_with_user
    template = _guest_template()
    skill = Skill.objects.create(key="arena-review-skill", name="审查技能")
    for _ in range(10):
        guest = Guest.objects.create(manor=manor, template=template, status=GuestStatus.IDLE)
        GuestSkill.objects.create(guest=guest, skill=skill)
    guests = list(Guest.objects.filter(manor=manor).select_related("template").order_by("id"))
    prefetched = list(
        Guest.objects.filter(manor=manor).select_related("template").prefetch_related("skills").order_by("id")
    )
    with CaptureQueriesContext(connection) as queries:
        current = [serialize_guest_skill_keys(guest) for guest in guests]
    with CaptureQueriesContext(connection) as prefetched_queries:
        existing_prefetch = [serialize_guest_skill_keys(guest) for guest in prefetched]
    assert current == existing_prefetch == [[skill.key]] * 10
    assert len(queries) == len(prefetched_queries) == 10
    print(
        f"arena_skill_candidate_profile vendor={connection.vendor} guests=10 skill_selects={len(queries)} "
        f"after_prefetch_selects={len(prefetched_queries)}"
    )
