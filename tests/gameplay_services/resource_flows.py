import logging

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import InsufficientResourceError
from gameplay.models import InventoryItem, Manor, PlayerTroop, ResourceEvent, ResourceType, TroopBankStorage
from gameplay.services.inventory.core import get_warehouse_grain_quantity
from gameplay.services.manor.core import ensure_manor
from gameplay.services.resources import (
    ResourceProductionBasis,
    grant_resources,
    preview_resource_grant,
    preview_resource_production,
    settle_resource_production_locked,
    spend_resources,
    sync_resource_production,
    sync_resource_production_batch,
)
from gameplay.tasks.resources import sync_resource_production_task
from gameplay.utils.resource_calculator import get_personnel_grain_cost_per_hour
from guests.models import Guest, GuestTemplate
from tests.gameplay_services.support import User, ensure_grain_template


@pytest.mark.django_db
def test_grant_resources():
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)
    initial_silver = manor.silver

    grant_resources(manor, {"silver": 100}, "测试奖励")
    manor.refresh_from_db()

    assert manor.silver == initial_silver + 100


@pytest.mark.django_db
def test_grant_resources_syncs_warehouse_grain_item():
    user = User.objects.create_user(username="grant_grain_sync_user", password="test123", email="g1@test.local")
    manor = ensure_manor(user)
    ensure_grain_template()

    initial_grain = manor.grain
    InventoryItem.objects.filter(
        manor=manor,
        template__key="grain",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).delete()

    credited = grant_resources(manor, {"grain": 75}, "发粮同步测试")
    manor.refresh_from_db(fields=["grain"])
    warehouse_grain = InventoryItem.objects.filter(
        manor=manor,
        template__key="grain",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).first()

    assert credited.get("grain", 0) > 0
    assert manor.grain > initial_grain
    assert warehouse_grain is not None
    assert warehouse_grain.quantity == manor.grain


@pytest.mark.django_db
def test_grant_resources_caps_and_logs_actual():
    user = User.objects.create_user(username="testuser2", password="test123")
    manor = ensure_manor(user)

    manor.silver_capacity = 100
    manor.silver = 95
    manor.save(update_fields=["silver_capacity", "silver"])

    credited = grant_resources(
        manor,
        {"silver": 20},
        note="容量测试",
        reason=ResourceEvent.Reason.TASK_REWARD,
    )
    manor.refresh_from_db()

    assert credited == {"silver": 5}
    assert manor.silver == 100

    event = ResourceEvent.objects.filter(
        manor=manor,
        resource_type=ResourceType.SILVER,
        reason=ResourceEvent.Reason.TASK_REWARD,
        note="容量测试",
    ).first()
    assert event is not None
    assert event.delta == 5


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_grant_resources_rejects_unknown_resource_in_debug():
    user = User.objects.create_user(username="resource_unknown_debug", password="test123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="unknown resource type: mystery"):
        grant_resources(manor, {"mystery": 10}, "未知资源测试")


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_grant_resources_skips_unknown_resource_and_logs_error(caplog):
    user = User.objects.create_user(username="resource_unknown_prod", password="test123")
    manor = ensure_manor(user)
    initial_silver = manor.silver

    with caplog.at_level(logging.ERROR):
        credited = grant_resources(manor, {"mystery": 10}, "未知资源测试")

    manor.refresh_from_db(fields=["silver"])
    assert credited == {}
    assert manor.silver == initial_silver
    assert "未知资源类型被跳过" in caplog.text


@pytest.mark.django_db
def test_grant_resources_rejects_false_rewards_payload():
    user = User.objects.create_user(username="resource_false_rewards", password="test123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid resource rewards"):
        grant_resources(manor, False, "坏奖励配置")  # type: ignore[arg-type]


@pytest.mark.django_db
def test_grant_resources_rejects_bool_reward_amount():
    user = User.objects.create_user(username="resource_bool_reward", password="test123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid resource amount: True"):
        grant_resources(manor, {"silver": True}, "坏奖励数量")  # type: ignore[arg-type]


@pytest.mark.django_db
def test_spend_resources_success():
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)
    manor.silver = 500
    manor.save()

    spend_resources(manor, {"silver": 100}, "测试消耗")
    manor.refresh_from_db()

    assert manor.silver == 400


@pytest.mark.django_db
def test_spend_resources_insufficient():
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)
    manor.silver = 50
    manor.save()

    with pytest.raises(InsufficientResourceError, match="银两不足"):
        spend_resources(manor, {"silver": 100}, "测试消耗")


@pytest.mark.django_db
def test_spend_resources_rejects_negative_cost():
    user = User.objects.create_user(username="resource_negative_cost", password="test123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid resource amount: -1"):
        spend_resources(manor, {"silver": -1}, "坏消耗")


@pytest.mark.django_db
def test_spend_resources_rejects_false_cost_payload():
    user = User.objects.create_user(username="resource_false_cost", password="test123")
    manor = ensure_manor(user)

    with pytest.raises(AssertionError, match="invalid resource cost"):
        spend_resources(manor, False, "坏消耗配置")  # type: ignore[arg-type]


@pytest.mark.django_db
def test_spend_resources_syncs_warehouse_grain_item():
    user = User.objects.create_user(username="spend_grain_sync_user", password="test123", email="s1@test.local")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()

    manor.grain = 300
    manor.save(update_fields=["grain"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 500},
    )

    spend_resources(manor, {"grain": 120}, "扣粮同步测试")
    manor.refresh_from_db(fields=["grain"])
    warehouse_grain = InventoryItem.objects.get(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert manor.grain == 380
    assert warehouse_grain.quantity == 380


@pytest.mark.django_db
def test_sync_resource_production():
    user = User.objects.create_user(username="testuser", password="test123")
    manor = ensure_manor(user)
    initial_silver = manor.silver

    manor.resource_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.save()

    sync_resource_production(manor)
    manor.refresh_from_db()

    assert manor.silver >= initial_silver


@pytest.mark.django_db
def test_sync_resource_production_can_skip_refresh_for_discarded_instance(monkeypatch):
    user = User.objects.create_user(username="resource_refresh_opt_out_user", password="test123")
    manor = ensure_manor(user)
    manor.resource_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.save(update_fields=["resource_updated_at"])

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {ResourceType.SILVER: 0, ResourceType.GRAIN: 0},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )

    def fail_refresh(*_args, **_kwargs):
        raise AssertionError("refresh_from_db should be skipped when refresh=False")

    monkeypatch.setattr(manor, "refresh_from_db", fail_refresh)
    sync_resource_production(manor, refresh=False)


@pytest.mark.django_db
def test_sync_resource_production_persist_false_projects_without_db_write(monkeypatch):
    user = User.objects.create_user(username="resource_projection_user", password="test123")
    manor = ensure_manor(user)
    original_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.resource_updated_at = original_updated_at
    manor.save(update_fields=["resource_updated_at"])
    initial_silver = manor.silver

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {ResourceType.SILVER: 120, ResourceType.GRAIN: 0},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)

    sync_resource_production(manor, persist=False)

    assert manor.silver == initial_silver + 120
    manor.refresh_from_db(fields=["silver", "resource_updated_at"])
    assert manor.silver == initial_silver
    assert manor.resource_updated_at == original_updated_at


@pytest.mark.django_db
def test_sync_resource_production_persist_false_projects_grain_ledger_without_db_write(monkeypatch):
    user = User.objects.create_user(username="grain_projection_user", password="test123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    original_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.grain = 100
    manor.resource_updated_at = original_updated_at
    manor.save(update_fields=["grain", "resource_updated_at"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 100},
    )

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {ResourceType.SILVER: 0, ResourceType.GRAIN: 120},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)

    sync_resource_production(manor, persist=False)

    assert manor.grain == 220
    assert get_warehouse_grain_quantity(manor) == 220
    manor.refresh_from_db(fields=["grain", "resource_updated_at"])
    assert manor.grain == 100
    assert manor.resource_updated_at == original_updated_at
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 100
    )


@pytest.mark.django_db
def test_resource_previews_do_not_mutate_manor_grain_projection(monkeypatch):
    user = User.objects.create_user(username="pure_resource_preview_user", password="test123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    original_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.grain = 17
    manor.resource_updated_at = original_updated_at
    manor.save(update_fields=["grain", "resource_updated_at"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 91},
    )
    manor.__dict__.pop("warehouse_grain_quantity", None)

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {ResourceType.SILVER: 0, ResourceType.GRAIN: 12},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)

    credited, overflow = preview_resource_grant(manor, {ResourceType.GRAIN: 5})
    produced = preview_resource_production(
        manor,
        now=original_updated_at + timezone.timedelta(hours=1),
    )

    assert credited == {ResourceType.GRAIN: 5}
    assert overflow == {}
    assert produced == {ResourceType.GRAIN: 12}
    assert manor.grain == 17
    assert "warehouse_grain_quantity" not in manor.__dict__


@pytest.mark.django_db
def test_spend_resources_applies_offline_production_before_balance_check(monkeypatch):
    user = User.objects.create_user(username="resource_spend_sync_user", password="test123")
    manor = ensure_manor(user)
    manor.silver = 50
    manor.resource_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.save(update_fields=["silver", "resource_updated_at"])

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {ResourceType.SILVER: 100, ResourceType.GRAIN: 0},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)

    spend_resources(manor, {"silver": 100}, "离线产出后扣费")
    manor.refresh_from_db(fields=["silver"])

    assert manor.silver == 50


@pytest.mark.django_db
def test_get_personnel_grain_cost_per_hour_counts_all_personnel():
    user = User.objects.create_user(username="personnel_cost_user", password="test123", email="p1@test.local")
    manor = ensure_manor(user)
    manor.retainer_count = 5
    manor.save(update_fields=["retainer_count"])

    guest_template = GuestTemplate.objects.create(
        key="personnel_cost_guest_tpl",
        name="耗粮测试门客",
        archetype="civil",
        rarity="green",
    )
    Guest.objects.create(manor=manor, template=guest_template)
    Guest.objects.create(manor=manor, template=guest_template)

    troop_template = TroopTemplate.objects.create(key="personnel_cost_guard_tpl", name="耗粮测试护院")
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=7)
    TroopBankStorage.objects.create(manor=manor, troop_template=troop_template, count=11)

    assert get_personnel_grain_cost_per_hour(manor) == 223


@pytest.mark.django_db
def test_sync_resource_production_allows_negative_grain_delta_and_clamps_to_zero(
    monkeypatch,
):
    user = User.objects.create_user(username="negative_grain_user", password="test123", email="p2@test.local")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 90
    manor.resource_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.save(update_fields=["grain", "resource_updated_at"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 90},
    )

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {ResourceType.GRAIN: 50, ResourceType.SILVER: 0},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 200,
    )
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)

    sync_resource_production(manor)
    manor.refresh_from_db()

    assert manor.grain == 0
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()
    event = (
        ResourceEvent.objects.filter(
            manor=manor,
            resource_type=ResourceType.GRAIN,
            reason=ResourceEvent.Reason.PRODUCE,
            note="离线产出",
        )
        .order_by("-id")
        .first()
    )
    assert event is not None
    assert event.delta == -90


@pytest.mark.django_db
def test_locked_resource_settlement_caps_positive_delta_and_preserves_upkeep(
    monkeypatch,
):
    user = User.objects.create_user(
        username="bounded_resource_settlement_user",
        password="test123",
    )
    manor = ensure_manor(user)
    ensure_grain_template()
    settled_at = timezone.now()
    manor.silver = 0
    manor.grain = 90
    manor.resource_updated_at = settled_at - timezone.timedelta(hours=1)
    manor.save(update_fields=["silver", "grain", "resource_updated_at"])

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {
            ResourceType.SILVER: 1_000,
            ResourceType.GRAIN: 50,
        },
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 200,
    )
    monkeypatch.setattr(
        "gameplay.services.resources.scale_value",
        lambda value: value,
    )

    with transaction.atomic():
        locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
        settled = settle_resource_production_locked(
            locked_manor,
            now=settled_at,
            positive_limits={
                ResourceType.SILVER: 25,
                ResourceType.GRAIN: 10,
            },
            note="有界产出测试",
        )

    manor.refresh_from_db(fields=["silver", "grain", "resource_updated_at"])
    assert settled == {
        ResourceType.GRAIN: -90,
        ResourceType.SILVER: 25,
    }
    assert (manor.silver, manor.grain) == (25, 0)
    assert manor.resource_updated_at == settled_at
    assert set(
        ResourceEvent.objects.filter(
            manor=manor,
            reason=ResourceEvent.Reason.PRODUCE,
            note="有界产出测试",
        ).values_list("resource_type", "delta")
    ) == {
        (ResourceType.GRAIN, -90),
        (ResourceType.SILVER, 25),
    }


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0, RESOURCE_SYNC_TRANSACTION_BATCH_SIZE=1)
def test_resource_sync_task_reuses_resolved_grain_template(monkeypatch):
    first_user = User.objects.create_user(username="resource_task_template_1", password="test123")
    second_user = User.objects.create_user(username="resource_task_template_2", password="test123")
    first_manor = ensure_manor(first_user)
    second_manor = ensure_manor(second_user)
    captured: list[tuple[tuple[int, ...], dict]] = []

    monkeypatch.setattr(
        "gameplay.tasks.resources.sync_resource_production_batch",
        lambda manor_ids, **kwargs: captured.append((tuple(int(manor_id) for manor_id in manor_ids), kwargs))
        or len(manor_ids),
    )

    assert sync_resource_production_task(limit=2) == 2
    assert len(captured) == 2
    assert {manor_id for item in captured for manor_id in item[0]} == {first_manor.id, second_manor.id}
    assert all(item[1]["grain_template_resolved"] is True for item in captured)
    assert captured[0][1]["grain_template"] is captured[1][1]["grain_template"]


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=60)
def test_resource_sync_task_filters_recent_manors_before_loading_template(monkeypatch):
    user = User.objects.create_user(username="resource_task_recent", password="test123")
    manor = ensure_manor(user)
    manor.resource_updated_at = timezone.now()
    manor.save(update_fields=["resource_updated_at"])

    def fail_if_batch_called(*_args, **_kwargs):
        raise AssertionError("recent manor must not enter the batch")

    def fail_if_template_loaded(*_args, **_kwargs):
        raise AssertionError("recent-only task must not load the grain template")

    monkeypatch.setattr("gameplay.tasks.resources.sync_resource_production_batch", fail_if_batch_called)
    monkeypatch.setattr("gameplay.tasks.resources.ItemTemplate.objects.filter", fail_if_template_loaded)

    assert sync_resource_production_task(limit=1) == 0


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=60)
def test_sync_resource_production_batch_skips_recent_manors_before_loading_basis(monkeypatch):
    first_user = User.objects.create_user(username="resource_batch_recent_1", password="test123")
    second_user = User.objects.create_user(username="resource_batch_recent_2", password="test123")
    first_manor = ensure_manor(first_user)
    second_manor = ensure_manor(second_user)
    settled_at = timezone.now()
    for manor in (first_manor, second_manor):
        manor.resource_updated_at = settled_at
        manor.save(update_fields=["resource_updated_at"])

    def fail_if_basis_loads(_manors):
        raise AssertionError("recent manors must short-circuit before loading production bases")

    monkeypatch.setattr("gameplay.services.resources.load_resource_production_bases", fail_if_basis_loads)

    with CaptureQueriesContext(connection) as captured:
        processed = sync_resource_production_batch(
            [first_manor.id, second_manor.id],
            grain_template_resolved=True,
            now=settled_at,
        )

    assert processed == 0
    manor_table = Manor._meta.db_table.lower()
    manor_queries = [
        query["sql"].lower()
        for query in captured
        if manor_table in query["sql"].lower() and "select" in query["sql"].lower()
    ]
    assert manor_queries
    assert any("resource_updated_at" in query for query in manor_queries)
    if connection.features.has_select_for_update:
        assert any("for update" in query for query in manor_queries)


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=60)
def test_sync_resource_production_batch_includes_exact_interval_boundary(monkeypatch):
    user = User.objects.create_user(username="resource_batch_boundary", password="test123")
    manor = ensure_manor(user)
    settled_at = timezone.now()
    manor.resource_updated_at = settled_at - timezone.timedelta(seconds=60)
    manor.save(update_fields=["resource_updated_at"])
    called: list[int] = []

    monkeypatch.setattr(
        "gameplay.services.resources.load_resource_production_bases",
        lambda manors: {int(current.pk): object() for current in manors},
    )
    monkeypatch.setattr(
        "gameplay.services.resources._sync_resource_production_locked",
        lambda current, **_kwargs: called.append(int(current.pk)),
    )

    assert (
        sync_resource_production_batch(
            [manor.id],
            grain_template_resolved=True,
            now=settled_at,
        )
        == 1
    )
    assert called == [manor.id]


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0)
def test_sync_resource_production_batch_loads_one_shared_basis(monkeypatch):
    first_user = User.objects.create_user(username="resource_batch_basis_1", password="test123")
    second_user = User.objects.create_user(username="resource_batch_basis_2", password="test123")
    first_manor = ensure_manor(first_user)
    second_manor = ensure_manor(second_user)
    first_initial_silver = first_manor.silver
    second_initial_silver = second_manor.silver
    settled_at = timezone.now()
    for manor in (first_manor, second_manor):
        manor.resource_updated_at = settled_at - timezone.timedelta(hours=1)
        manor.save(update_fields=["resource_updated_at"])

    grain_template = ensure_grain_template()
    basis = ResourceProductionBasis(
        hourly_rates=((ResourceType.SILVER, 120.0),),
        personnel_grain_cost_per_hour=0,
    )
    loaded_batches: list[tuple[int, ...]] = []

    def load_shared_basis(manors):
        manor_ids = tuple(int(manor.pk) for manor in manors)
        loaded_batches.append(manor_ids)
        return {manor_id: basis for manor_id in manor_ids}

    monkeypatch.setattr("gameplay.services.resources.load_resource_production_bases", load_shared_basis)

    processed = sync_resource_production_batch(
        [first_manor.id, second_manor.id],
        grain_template=grain_template,
        grain_template_resolved=True,
        now=settled_at,
    )

    assert processed == 2
    assert len(loaded_batches) == 1
    assert set(loaded_batches[0]) == {first_manor.id, second_manor.id}
    first_manor.refresh_from_db(fields=["silver"])
    second_manor.refresh_from_db(fields=["silver"])
    assert first_manor.silver > first_initial_silver
    assert second_manor.silver > second_initial_silver


@pytest.mark.django_db
def test_locked_resource_settlement_reuses_resolved_grain_template_for_ledger_write():
    user = User.objects.create_user(username="resource_locked_template_reuse", password="test123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    settled_at = timezone.now()
    manor.grain = 0
    manor.resource_updated_at = settled_at - timezone.timedelta(hours=1)
    manor.save(update_fields=["grain", "resource_updated_at"])

    production_basis = ResourceProductionBasis(
        hourly_rates=((ResourceType.GRAIN, 100.0),),
        personnel_grain_cost_per_hour=0,
    )

    with transaction.atomic():
        locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
        with CaptureQueriesContext(connection) as captured:
            settled = settle_resource_production_locked(
                locked_manor,
                now=settled_at,
                production_basis=production_basis,
                grain_template=grain_template,
                grain_template_resolved=True,
            )

    assert settled == {ResourceType.GRAIN: 100}
    item_template_table = grain_template._meta.db_table.lower()
    assert not any(item_template_table in query["sql"].lower() for query in captured.captured_queries)
