from __future__ import annotations

import threading

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from gameplay.constants import BuildingKeys
from gameplay.models import Building, BuildingType, Manor, ResourceEvent, ResourceType
from gameplay.services.manor import core as manor_core

pytestmark = [pytest.mark.integration]


def _require_isolated_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("building upgrade concurrency evidence requires MySQL row locks")
    if str(connection.settings_dict["NAME"]) != "test_webgame":
        pytest.skip("building upgrade concurrency evidence only runs on test_webgame")


def _ensure_silver_vault(manor: Manor) -> Building:
    building_type, _created = BuildingType.objects.update_or_create(
        key=BuildingKeys.SILVER_VAULT,
        defaults={
            "name": "银库",
            "category": "storage",
            "resource_type": ResourceType.SILVER,
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            "base_upgrade_time": 300,
            "time_growth": 1.2,
            "base_cost": {ResourceType.SILVER: 1_100},
            "cost_growth": 1.35,
            "icon": "silver_vault",
        },
    )
    building, _created = Building.objects.get_or_create(
        manor=manor,
        building_type=building_type,
    )
    return Building.objects.select_related("building_type").get(pk=building.pk)


def _ensure_granary(manor: Manor) -> Building:
    building_type, _created = BuildingType.objects.update_or_create(
        key=BuildingKeys.GRANARY,
        defaults={
            "name": "粮仓",
            "category": "storage",
            "resource_type": ResourceType.GRAIN,
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            "base_upgrade_time": 300,
            "time_growth": 1.2,
            "base_cost": {ResourceType.GRAIN: 1_100},
            "cost_growth": 1.35,
            "icon": "granary",
        },
    )
    building, _created = Building.objects.get_or_create(
        manor=manor,
        building_type=building_type,
    )
    return Building.objects.select_related("building_type").get(pk=building.pk)


@pytest.mark.django_db(transaction=True)
def test_same_building_quote_applies_once_under_concurrent_manor_then_building_locks(
    django_user_model,
) -> None:
    _require_isolated_mysql()
    user = django_user_model.objects.create_user(
        username="building_upgrade_primitive_concurrent",
        password="pass12345",
    )
    manor = manor_core.ensure_manor(user)
    fixed_now = timezone.now()
    Manor.objects.filter(pk=manor.pk).update(
        silver=100_000,
        grain=100_000,
        silver_capacity=100_000,
        grain_capacity=100_000,
        prestige=0,
        prestige_silver_spent=0,
        resource_updated_at=fixed_now,
    )
    manor.refresh_from_db()
    building = _ensure_silver_vault(manor)
    quote = manor_core.quote_building_upgrade(manor, building)
    costs = dict(quote.resource_cost)
    before_level = building.level
    start = threading.Barrier(2)
    successes: list[manor_core.BuildingUpgradeResult] = []
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                locked_building = (
                    Building.objects.select_for_update()
                    .select_related("building_type")
                    .get(pk=building.pk, manor_id=locked_manor.pk)
                )
                result = manor_core.apply_building_upgrade_locked(
                    locked_manor,
                    locked_building,
                    quote,
                    sync_production=False,
                )
            with result_guard:
                successes.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    workers = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert all(not worker.is_alive() for worker in workers), "building upgrade workers deadlocked"
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], manor_core.BuildingUpgradeQuoteStaleError)
    assert successes[0].previous_level == before_level
    assert successes[0].level == before_level + 1

    manor.refresh_from_db()
    building.refresh_from_db()
    assert building.level == before_level + 1
    assert building.is_upgrading is False
    assert building.upgrade_complete_at is None
    assert manor.silver == 100_000 - costs[ResourceType.SILVER]
    assert manor.grain == 100_000 - costs.get(ResourceType.GRAIN, 0)
    assert manor.prestige_silver_spent == costs[ResourceType.SILVER]
    assert ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.UPGRADE_COST,
    ).count() == len(costs)
    assert set(
        ResourceEvent.objects.filter(
            manor=manor,
            reason=ResourceEvent.Reason.UPGRADE_COST,
        ).values_list("resource_type", "delta")
    ) == {(resource, -amount) for resource, amount in costs.items()}


@pytest.mark.django_db(transaction=True)
def test_distinct_public_building_upgrades_quote_after_the_manor_lock(
    django_user_model,
    monkeypatch,
) -> None:
    _require_isolated_mysql()
    user = django_user_model.objects.create_user(
        username="building_upgrade_public_concurrent",
        password="pass12345",
    )
    manor = manor_core.ensure_manor(user)
    fixed_now = timezone.now()
    Manor.objects.filter(pk=manor.pk).update(
        silver=100_000,
        grain=100_000,
        silver_capacity=100_000,
        grain_capacity=100_000,
        resource_updated_at=fixed_now,
    )
    buildings = (_ensure_silver_vault(manor), _ensure_granary(manor))
    original_quote = manor_core.quote_building_upgrade
    pre_lock_quote_barrier = threading.Barrier(2)
    start = threading.Barrier(2)
    successes: list[int] = []
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    def _quote_after_lock(*args, **kwargs):
        quote = original_quote(*args, **kwargs)
        if not transaction.get_connection().in_atomic_block:
            pre_lock_quote_barrier.wait(timeout=10)
        return quote

    monkeypatch.setattr(manor_core, "quote_building_upgrade", _quote_after_lock)
    monkeypatch.setattr(manor_core, "schedule_building_completion", lambda *_args, **_kwargs: None)

    def _worker(building_id: int) -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            local_building = Building.objects.select_related("manor", "building_type").get(pk=building_id)
            manor_core.start_upgrade(local_building)
            with result_guard:
                successes.append(building_id)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    workers = [threading.Thread(target=_worker, args=(building.pk,), daemon=True) for building in buildings]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert all(not worker.is_alive() for worker in workers), "building upgrade workers deadlocked"
    assert errors == []
    assert sorted(successes) == sorted(building.pk for building in buildings)
    assert (
        Building.objects.filter(
            pk__in=[building.pk for building in buildings],
            is_upgrading=True,
            upgrade_complete_at__isnull=False,
        ).count()
        == 2
    )
