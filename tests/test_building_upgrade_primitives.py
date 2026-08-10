from __future__ import annotations

import math
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.exceptions import ActionPointsInsufficientError, BuildingConcurrentUpgradeLimitError, BuildingMaxLevelError
from gameplay.constants import BUILDING_MAX_LEVELS, MAX_CONCURRENT_BUILDING_UPGRADES, BuildingKeys
from gameplay.models import Building, Manor, Message, PlayerTechnology, ResourceEvent, ResourceType
from gameplay.services.action_points import ACTION_POINT_BUILDING_UPGRADE_COST
from gameplay.services.manor import core as manor_core
from gameplay.services.utils import messages as message_service
from gameplay.services.utils.cache import CacheKeys


def _building(manor: Manor, building_key: str) -> Building:
    return manor.buildings.select_related("building_type").get(building_type__key=building_key)


def _fund_manor(
    manor: Manor,
    *,
    now: datetime | None = None,
    silver: int = 50_000,
    grain: int = 50_000,
    capacity: int = 50_000,
    prestige: int = 0,
    prestige_silver_spent: int = 0,
) -> datetime:
    fixed_now = now or timezone.now()
    Manor.objects.filter(pk=manor.pk).update(
        silver=silver,
        grain=grain,
        silver_capacity=capacity,
        grain_capacity=capacity,
        prestige=prestige,
        prestige_silver_spent=prestige_silver_spent,
        resource_updated_at=fixed_now,
    )
    manor.refresh_from_db()
    return fixed_now


def _apply_quote(
    manor: Manor,
    building: Building,
    quote: manor_core.BuildingUpgradeQuote,
) -> manor_core.BuildingUpgradeResult:
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        locked_building = (
            Building.objects.select_for_update()
            .select_related("building_type")
            .get(pk=building.pk, manor_id=locked_manor.pk)
        )
        return manor_core.apply_building_upgrade_locked(
            locked_manor,
            locked_building,
            quote,
            sync_production=False,
        )


@pytest.mark.django_db
def test_quote_building_upgrade_freezes_architecture_discount_and_citang_duration(
    manor_factory,
    settings,
) -> None:
    settings.GAME_TIME_MULTIPLIER = 1.0
    manor, _user = manor_factory(username="building_quote_modifiers")
    PlayerTechnology.objects.update_or_create(
        manor=manor,
        tech_key="architecture",
        defaults={"level": 2},
    )
    citang = _building(manor, BuildingKeys.CITANG)
    citang.level = 5
    citang.save(update_fields=["level"])
    manor.invalidate_building_cache()
    building = _building(manor, BuildingKeys.SILVER_VAULT)

    quote = manor_core.quote_building_upgrade(manor, building)

    base_cost = building.next_level_cost()
    expected_cost = {resource: max(1, math.ceil(amount * 0.9)) for resource, amount in base_cost.items()}
    base_duration = building.next_level_duration()
    assert quote.manor_id == manor.pk
    assert quote.building_id == building.pk
    assert quote.building_type_id == building.building_type_id
    assert quote.building_key == BuildingKeys.SILVER_VAULT
    assert quote.current_level == building.level
    assert quote.target_level == building.level + 1
    assert quote.max_level == BUILDING_MAX_LEVELS[BuildingKeys.SILVER_VAULT]
    assert dict(quote.base_cost) == base_cost
    assert dict(quote.resource_cost) == expected_cost
    assert quote.cost_reduction == pytest.approx(0.10)
    assert quote.base_duration == base_duration
    assert quote.duration_seconds == max(1, int(base_duration * 0.8))
    assert quote.upgrading_count == 0
    assert quote.to_payload()["resource_cost"] == expected_cost


@pytest.mark.django_db
def test_quote_building_upgrade_enforces_max_level_and_concurrent_slot(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="building_quote_eligibility")
    citang = _building(manor, BuildingKeys.CITANG)
    citang.level = BUILDING_MAX_LEVELS[BuildingKeys.CITANG]
    citang.save(update_fields=["level"])

    with pytest.raises(BuildingMaxLevelError):
        manor_core.quote_building_upgrade(manor, citang)

    target = _building(manor, BuildingKeys.SILVER_VAULT)
    blocking_ids = list(
        manor.buildings.exclude(pk=target.pk)
        .order_by("pk")
        .values_list("pk", flat=True)[:MAX_CONCURRENT_BUILDING_UPGRADES]
    )
    assert len(blocking_ids) == MAX_CONCURRENT_BUILDING_UPGRADES
    Building.objects.filter(pk__in=blocking_ids).update(
        is_upgrading=True,
        upgrade_complete_at=timezone.now() + timedelta(hours=1),
    )

    with pytest.raises(BuildingConcurrentUpgradeLimitError):
        manor_core.quote_building_upgrade(manor, target)


@pytest.mark.django_db(transaction=True)
def test_apply_building_upgrade_locked_requires_outer_transaction(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="building_apply_requires_transaction")
    _fund_manor(manor)
    building = _building(manor, BuildingKeys.FORGE)
    quote = manor_core.quote_building_upgrade(manor, building)

    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        manor_core.apply_building_upgrade_locked(
            manor,
            building,
            quote,
            sync_production=False,
        )


@pytest.mark.django_db
def test_apply_building_upgrade_locked_commits_one_level_cost_events_and_prestige_without_async_side_effects(
    manor_factory,
    monkeypatch,
) -> None:
    manor, _user = manor_factory(username="building_apply_success")
    _fund_manor(
        manor,
        prestige=0,
        prestige_silver_spent=900,
    )
    building = _building(manor, BuildingKeys.FORGE)
    quote = manor_core.quote_building_upgrade(manor, building)
    costs = dict(quote.resource_cost)
    before_level = building.level
    before_messages = Message.objects.filter(manor=manor).count()
    schedule = Mock()
    notify = Mock()
    create_message = Mock()
    monkeypatch.setattr(manor_core, "schedule_building_completion", schedule)
    monkeypatch.setattr(manor_core, "notify_user", notify)
    monkeypatch.setattr(message_service, "create_message", create_message)

    result = _apply_quote(manor, building, quote)

    manor.refresh_from_db()
    building.refresh_from_db()
    assert building.level == before_level + 1
    assert building.is_upgrading is False
    assert building.upgrade_complete_at is None
    assert manor.silver == 50_000 - costs[ResourceType.SILVER]
    assert manor.grain == 50_000 - costs[ResourceType.GRAIN]
    expected_spent = 900 + costs[ResourceType.SILVER]
    assert manor.prestige_silver_spent == expected_spent
    assert manor.prestige == expected_spent // 1000
    assert result.previous_level == before_level
    assert result.level == before_level + 1
    assert result.resource_cost == quote.resource_cost
    assert result.prestige_gained == expected_spent // 1000
    assert result.to_payload()["resource_cost"] == costs

    events = set(
        ResourceEvent.objects.filter(
            manor=manor,
            reason=ResourceEvent.Reason.UPGRADE_COST,
        ).values_list("resource_type", "delta", "note")
    )
    assert events == {(resource, -amount, building.building_type.name) for resource, amount in costs.items()}
    schedule.assert_not_called()
    notify.assert_not_called()
    create_message.assert_not_called()
    assert Message.objects.filter(manor=manor).count() == before_messages


@pytest.mark.parametrize(
    ("building_key", "capacity_field", "is_silver_vault"),
    [
        (BuildingKeys.SILVER_VAULT, "silver_capacity", True),
        (BuildingKeys.GRANARY, "grain_capacity", False),
    ],
)
@pytest.mark.django_db
def test_apply_building_upgrade_locked_recalculates_storage_and_invalidates_caches_after_commit(
    manor_factory,
    django_capture_on_commit_callbacks,
    monkeypatch,
    building_key: str,
    capacity_field: str,
    is_silver_vault: bool,
) -> None:
    manor, _user = manor_factory(username=f"building_capacity_{building_key}")
    _fund_manor(manor, silver=10_000, grain=10_000, capacity=20_000)
    building = _building(manor, building_key)
    quote = manor_core.quote_building_upgrade(manor, building)
    cache_key = CacheKeys.home_hourly_rates(int(manor.pk))
    cache.set(cache_key, "stale", timeout=60)
    monkeypatch.setattr(
        "gameplay.services.manor.prestige._emit_prestige_change_committed",
        lambda **_kwargs: None,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            assert locked_manor.get_building_level(building_key) == building.level
            assert hasattr(locked_manor, "_building_levels")
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
            assert not hasattr(locked_manor, "_building_levels")

    manor.refresh_from_db()
    building.refresh_from_db()
    expected_capacity = manor_core.calculate_building_capacity(
        building.level,
        is_silver_vault=is_silver_vault,
    )
    assert getattr(manor, capacity_field) == expected_capacity
    assert getattr(result, capacity_field) == expected_capacity
    assert cache.get(cache_key) is None


@pytest.mark.django_db
def test_apply_building_upgrade_locked_rejects_stale_quote_before_writes(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="building_apply_stale")
    _fund_manor(manor, prestige=3, prestige_silver_spent=3_400)
    building = _building(manor, BuildingKeys.SILVER_VAULT)
    quote = manor_core.quote_building_upgrade(manor, building)
    PlayerTechnology.objects.create(
        manor=manor,
        tech_key="architecture",
        level=1,
    )
    before = (
        manor.silver,
        manor.grain,
        manor.prestige,
        manor.prestige_silver_spent,
        building.level,
    )

    with pytest.raises(manor_core.BuildingUpgradeQuoteStaleError):
        _apply_quote(manor, building, quote)

    manor.refresh_from_db()
    building.refresh_from_db()
    assert (
        manor.silver,
        manor.grain,
        manor.prestige,
        manor.prestige_silver_spent,
        building.level,
    ) == before
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.UPGRADE_COST,
    ).exists()


@pytest.mark.django_db
def test_apply_building_upgrade_locked_rolls_back_resources_events_prestige_level_capacity_and_cache(
    manor_factory,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    manor, _user = manor_factory(username="building_apply_rollback")
    _fund_manor(
        manor,
        silver=20_000,
        grain=20_000,
        capacity=20_000,
        prestige=0,
        prestige_silver_spent=900,
    )
    building = _building(manor, BuildingKeys.SILVER_VAULT)
    quote = manor_core.quote_building_upgrade(manor, building)
    before = (
        manor.silver,
        manor.grain,
        manor.prestige,
        manor.prestige_silver_spent,
        manor.silver_capacity,
        manor.grain_capacity,
        building.level,
    )
    cache_key = CacheKeys.home_hourly_rates(int(manor.pk))
    cache.set(cache_key, "must-survive-rollback", timeout=60)
    original_apply = manor_core._apply_building_upgrade_result_locked

    def _fail_after_domain_writes(locked_manor: Manor, locked_building: Building, **kwargs):
        original_apply(locked_manor, locked_building, **kwargs)
        raise RuntimeError("injected building upgrade second phase failure")

    monkeypatch.setattr(
        manor_core,
        "_apply_building_upgrade_result_locked",
        _fail_after_domain_writes,
    )

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError, match="second phase failure"):
            _apply_quote(manor, building, quote)

    manor.refresh_from_db()
    building.refresh_from_db()
    assert (
        manor.silver,
        manor.grain,
        manor.prestige,
        manor.prestige_silver_spent,
        manor.silver_capacity,
        manor.grain_capacity,
        building.level,
    ) == before
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.UPGRADE_COST,
    ).exists()
    assert cache.get(cache_key) == "must-survive-rollback"
    cache.delete(cache_key)


@pytest.mark.django_db
def test_start_upgrade_matches_quote_cost_and_duration(
    manor_factory,
    monkeypatch,
    settings,
) -> None:
    settings.GAME_TIME_MULTIPLIER = 1.0
    manor, _user = manor_factory(username="building_start_quote_parity")
    fixed_now = _fund_manor(manor, silver=100_000, grain=100_000, capacity=100_000)
    PlayerTechnology.objects.update_or_create(
        manor=manor,
        tech_key="architecture",
        defaults={"level": 2},
    )
    citang = _building(manor, BuildingKeys.CITANG)
    citang.level = 3
    citang.save(update_fields=["level"])
    manor.invalidate_building_cache()
    building = _building(manor, BuildingKeys.FORGE)
    quote = manor_core.quote_building_upgrade(manor, building)
    costs = dict(quote.resource_cost)
    before_level = building.level
    schedule = Mock()
    monkeypatch.setattr(manor_core, "schedule_building_completion", schedule)
    monkeypatch.setattr(manor_core.timezone, "now", lambda: fixed_now)

    manor_core.start_upgrade(building)

    manor.refresh_from_db()
    building.refresh_from_db()
    assert building.level == before_level
    assert building.is_upgrading is True
    assert building.upgrade_complete_at == fixed_now + timedelta(seconds=quote.duration_seconds)
    assert manor.silver == 100_000 - costs[ResourceType.SILVER]
    assert manor.grain == 100_000 - costs[ResourceType.GRAIN]
    assert set(
        ResourceEvent.objects.filter(
            manor=manor,
            reason=ResourceEvent.Reason.UPGRADE_COST,
        ).values_list("resource_type", "delta")
    ) == {(resource, -amount) for resource, amount in costs.items()}
    schedule.assert_called_once()
    scheduled_building, scheduled_duration = schedule.call_args.args
    assert scheduled_building.pk == building.pk
    assert scheduled_duration == quote.duration_seconds


@pytest.mark.django_db
def test_start_upgrade_consumes_building_action_points(
    manor_factory,
    monkeypatch,
) -> None:
    manor, _user = manor_factory(username="building_action_points_cost")
    _fund_manor(manor, silver=100_000, grain=100_000, capacity=100_000)
    now = timezone.now()
    Manor.objects.filter(pk=manor.pk).update(
        action_points=50,
        action_points_updated_at=now,
    )
    manor.refresh_from_db()
    building = _building(manor, BuildingKeys.FORGE)
    monkeypatch.setattr(manor_core, "schedule_building_completion", Mock())

    manor_core.start_upgrade(building)

    manor.refresh_from_db()
    assert manor.action_points == 50 - ACTION_POINT_BUILDING_UPGRADE_COST


@pytest.mark.django_db
def test_start_upgrade_rejects_when_building_action_points_are_insufficient(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="building_action_points_insufficient")
    _fund_manor(manor, silver=100_000, grain=100_000, capacity=100_000)
    now = timezone.now()
    Manor.objects.filter(pk=manor.pk).update(
        action_points=ACTION_POINT_BUILDING_UPGRADE_COST - 1,
        action_points_updated_at=now,
    )
    manor.refresh_from_db()
    building = _building(manor, BuildingKeys.FORGE)
    before_resources = (manor.silver, manor.grain)

    with pytest.raises(ActionPointsInsufficientError, match="无法升级建筑"):
        manor_core.start_upgrade(building)

    manor.refresh_from_db()
    building.refresh_from_db()
    assert manor.action_points == ACTION_POINT_BUILDING_UPGRADE_COST - 1
    assert (manor.silver, manor.grain) == before_resources
    assert building.is_upgrading is False
