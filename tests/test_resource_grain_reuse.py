from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import DateTimeField, Value
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from gameplay.models import Building, BuildingType, InventoryItem, ItemTemplate, Manor, ResourceEvent, ResourceType
from gameplay.services import resources
from gameplay.services.inventory import core as inventory_core

pytestmark = pytest.mark.django_db
BASELINE_DIRECTORY = Path(__file__).parent / "fixtures" / "resource_grain_reuse_baseline"
BASELINE_SHA256 = {
    "inventory_core.py.txt": "33f8890db7c7d10a139f7ebda42e1dca042526de883a36dc433532aacee661a9",
    "resources.py.txt": "ba3a2d32e8d25b46a2f8b3efea11e0295e5837cae19d1f6e3de97290b79c4f2d",
}


def _load_baseline_module(name, filename):
    path = BASELINE_DIRECTORY / filename
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == BASELINE_SHA256[filename]
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def baseline_modules():
    inventory = _load_baseline_module("gameplay.services.inventory._grain_reuse_baseline", "inventory_core.py.txt")
    settlement = _load_baseline_module("gameplay.services._grain_reuse_baseline", "resources.py.txt")
    return SimpleNamespace(inventory=inventory, resources=settlement)


@contextmanager
def _baseline_service(baseline):
    # 原资源模块按相对路径导入库存服务，显式绑定完整原源码避免调用优化后的实现。
    with (
        patch.object(
            inventory_core,
            "get_warehouse_grain_quantity_locked",
            baseline.inventory.get_warehouse_grain_quantity_locked,
        ),
        patch.object(
            inventory_core,
            "set_warehouse_grain_quantity_locked",
            baseline.inventory.set_warehouse_grain_quantity_locked,
        ),
    ):
        yield baseline.resources


def _freeze_clock(monkeypatch, baseline, now):
    monkeypatch.setattr(timezone, "now", lambda: now)

    def sql_now():
        return Value(now, output_field=DateTimeField())

    monkeypatch.setattr(inventory_core, "Now", sql_now)
    monkeypatch.setattr(baseline.inventory, "Now", sql_now)


def _world(size, now):
    suffix = uuid4().hex[:12]
    usernames = [f"resource-reuse-{suffix}-{index}" for index in range(size)]
    User.objects.bulk_create([User(username=username) for username in usernames])
    users = list(User.objects.filter(username__in=usernames).order_by("id"))
    Manor.objects.bulk_create(
        [Manor(user=user, grain=1_000, silver=5_000, resource_updated_at=now - timedelta(hours=1)) for user in users]
    )
    manors = list(Manor.objects.filter(user_id__in=[user.pk for user in users]).order_by("id"))
    grain_template, _created = ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食"})
    InventoryItem.objects.bulk_create(
        [
            InventoryItem(
                manor=manor,
                template=grain_template,
                quantity=1_000,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            for manor in manors
        ]
    )
    for resource, rate in ((ResourceType.GRAIN, 100), (ResourceType.SILVER, 60)):
        building_type = BuildingType.objects.create(
            key=f"reuse_{resource}_{suffix}", name="结算基准建筑", resource_type=resource, base_rate_per_hour=rate
        )
        Building.objects.bulk_create([Building(manor=manor, building_type=building_type) for manor in manors])
    return manors, grain_template


def _ledger_snapshot(manor_ids):
    # MySQL 回滚不归还自增 ID；按业务归属及写入顺序比较所有其余账本字段。
    def without_id(rows):
        return [{key: value for key, value in row.items() if key != "id"} for row in rows]

    return {
        "manors": list(Manor.objects.filter(pk__in=manor_ids).order_by("id").values()),
        "inventory": without_id(
            InventoryItem.objects.filter(manor_id__in=manor_ids)
            .order_by("manor_id", "template_id", "storage_location")
            .values()
        ),
        "events": without_id(ResourceEvent.objects.filter(manor_id__in=manor_ids).order_by("id").values()),
    }


def _count_grain_ledger_selects(queries):
    inventory_table = InventoryItem._meta.db_table.lower()
    grain_ledger_filter_columns = tuple(
        InventoryItem._meta.get_field(field_name).column.lower()
        for field_name in ("manor", "template", "storage_location")
    )
    count = 0
    for query in queries:
        sql = query["sql"]
        lowered_sql = sql.lower()
        where_clause = lowered_sql.partition(" where ")[2]
        if (
            sql.lstrip().upper().startswith("SELECT")
            and inventory_table in lowered_sql
            and where_clause
            and all(column in where_clause for column in grain_ledger_filter_columns)
        ):
            count += 1
    return count


def _settle_chunks(service, manor_ids, grain_template, now):
    processed = 0
    for start in range(0, len(manor_ids), 50):
        processed += service.sync_resource_production_batch(
            manor_ids[start : start + 50], grain_template=grain_template, grain_template_resolved=True, now=now
        )
    return processed


@override_settings(GAME_TIME_MULTIPLIER=1, RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0)
@pytest.mark.parametrize("size", [1, 50, 500])
def test_resource_reuse_matches_independent_original_source_and_reuses_locked_grain_row(
    baseline_modules, monkeypatch, size
):
    now = timezone.now()
    _freeze_clock(monkeypatch, baseline_modules, now)
    manors, grain_template = _world(size, now)
    manor_ids = [manor.pk for manor in manors]
    with transaction.atomic():
        with _baseline_service(baseline_modules) as service, CaptureQueriesContext(connection) as old_queries:
            expected = _settle_chunks(service, manor_ids, grain_template, now)
        expected_state = _ledger_snapshot(manor_ids)
        transaction.set_rollback(True)

    with transaction.atomic():
        with CaptureQueriesContext(connection) as new_queries:
            actual = _settle_chunks(resources, manor_ids, grain_template, now)
        assert _ledger_snapshot(manor_ids) == expected_state
        transaction.set_rollback(True)

    assert actual == expected == size
    old_grain_selects = _count_grain_ledger_selects(old_queries)
    new_grain_selects = _count_grain_ledger_selects(new_queries)
    assert old_grain_selects - new_grain_selects == size
    # The current batch path also reads production inputs once per chunk. Those
    # fixed reads do not scale with manor count, so total-query savings are not
    # exactly one per manor; at batch sizes, the row reuse must still reduce
    # the overall query count.
    if size >= 50:
        assert len(new_queries) < len(old_queries)
    old_selects = sum(query["sql"].lstrip().upper().startswith("SELECT") for query in old_queries)
    new_selects = sum(query["sql"].lstrip().upper().startswith("SELECT") for query in new_queries)
    measurements: dict[str, list[float]] = {"original": [], "optimized": []}
    # 先预热两条完整路径，所有测量均回滚到同一初始账本；建数和快照不计时。
    for iteration in range(6):
        for mode in measurements:
            with transaction.atomic():
                if mode == "original":
                    with _baseline_service(baseline_modules) as service:
                        started = perf_counter()
                        _settle_chunks(service, manor_ids, grain_template, now)
                        elapsed_ms = (perf_counter() - started) * 1_000
                else:
                    started = perf_counter()
                    _settle_chunks(resources, manor_ids, grain_template, now)
                    elapsed_ms = (perf_counter() - started) * 1_000
                if iteration > 0:
                    measurements[mode].append(elapsed_ms)
                transaction.set_rollback(True)
    print(
        json.dumps(
            {
                "benchmark": "resource_grain_locked_row_reuse",
                "vendor": connection.vendor,
                "manors": size,
                "transaction_chunk": 50,
                "original_sql": len(old_queries),
                "optimized_sql": len(new_queries),
                "original_grain_ledger_selects": old_grain_selects,
                "optimized_grain_ledger_selects": new_grain_selects,
                "original_selects": old_selects,
                "optimized_selects": new_selects,
                "original_median_ms": round(median(measurements["original"]), 3),
                "optimized_median_ms": round(median(measurements["optimized"]), 3),
                "warm_samples": 5,
                "baseline_sha256": BASELINE_SHA256,
                "runtime_sha256": {
                    module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                    for module in (resources, inventory_core)
                },
            },
            sort_keys=True,
        )
    )


@override_settings(GAME_TIME_MULTIPLIER=1, RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0)
@pytest.mark.parametrize(
    "scenario",
    [
        "missing_legacy_row",
        "missing_zero_row",
        "missing_template",
        "zero_deletion",
        "compatibility_mismatch",
        "no_grain_delta",
        "capacity",
        "over_capacity",
        "positive_limits",
        "non_advancing_time",
    ],
)
def test_resource_reuse_preserves_all_ledger_and_event_fields_at_boundaries(baseline_modules, monkeypatch, scenario):
    now = timezone.now()
    _freeze_clock(monkeypatch, baseline_modules, now)
    manors, grain_template = _world(1, now)
    manor = manors[0]
    if scenario.startswith("missing_"):
        InventoryItem.objects.filter(manor=manor).delete()
    if scenario == "missing_zero_row":
        Manor.objects.filter(pk=manor.pk).update(grain=0)
    elif scenario == "missing_template":
        grain_template.delete()
        grain_template = None
    elif scenario == "compatibility_mismatch":
        Manor.objects.filter(pk=manor.pk).update(grain=7)
    elif scenario == "capacity":
        Manor.objects.filter(pk=manor.pk).update(grain_capacity=1_030, silver_capacity=5_020)
    elif scenario == "over_capacity":
        Manor.objects.filter(pk=manor.pk).update(grain_capacity=700, silver_capacity=3_000)
    elif scenario == "non_advancing_time":
        Manor.objects.filter(pk=manor.pk).update(resource_updated_at=now)
    basis = resources.ResourceProductionBasis(
        hourly_rates=((ResourceType.GRAIN, 100.0), (ResourceType.SILVER, 60.0)),
        personnel_grain_cost_per_hour=(
            2_000 if scenario == "zero_deletion" else 100 if scenario == "no_grain_delta" else 0
        ),
    )
    kwargs = {
        "now": now,
        "grain_template": grain_template,
        "grain_template_resolved": True,
        "production_basis": basis,
        "positive_limits": {"grain": 10, "silver": 5} if scenario == "positive_limits" else None,
    }
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        with _baseline_service(baseline_modules) as service:
            expected = service.settle_resource_production_locked(locked_manor, **kwargs)
        expected_state = _ledger_snapshot([manor.pk])
        transaction.set_rollback(True)
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        actual = resources.settle_resource_production_locked(locked_manor, **kwargs)
        assert actual == expected
        assert _ledger_snapshot([manor.pk]) == expected_state


@override_settings(GAME_TIME_MULTIPLIER=1, RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0)
@pytest.mark.parametrize("template_key", ["GrAiN", "gráin", "grain "])
def test_resource_reuse_preserves_grain_templates_resolved_by_database_collation(
    baseline_modules, monkeypatch, template_key
):
    now = timezone.now()
    _freeze_clock(monkeypatch, baseline_modules, now)
    manors, grain_template = _world(1, now)
    manor = manors[0]
    ItemTemplate.objects.filter(pk=grain_template.pk).update(key=template_key)
    grain_template.refresh_from_db()
    resolved_template = ItemTemplate.objects.filter(key="grain").first()
    collation = "backend default"
    if connection.vendor == "mysql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                [ItemTemplate._meta.db_table, "key"],
            )
            collation = cursor.fetchone()[0]
    print(
        json.dumps(
            {
                "grain_template_lookup": template_key,
                "vendor": connection.vendor,
                "column_collation": collation,
                "matched": resolved_template is not None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    basis = resources.ResourceProductionBasis(
        hourly_rates=((ResourceType.GRAIN, 100.0),), personnel_grain_cost_per_hour=0
    )
    # 保留原调用方隐式查模板的接口；显式传入模板时原解析器的校验仍原样保留。
    kwargs = {"now": now, "production_basis": basis}
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        with _baseline_service(baseline_modules) as service:
            expected = service.settle_resource_production_locked(locked_manor, **kwargs)
        expected_state = _ledger_snapshot([manor.pk])
        transaction.set_rollback(True)
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        actual = resources.settle_resource_production_locked(locked_manor, **kwargs)
        assert actual == expected == {ResourceType.GRAIN: 100}
        assert _ledger_snapshot([manor.pk]) == expected_state
        # 大小写/重音/PAD 行为由真实列排序规则决定；未命中时保持异名库存原数量。
        assert InventoryItem.objects.get(manor=manor, template=grain_template).quantity == (
            1_100 if resolved_template is not None else 1_000
        )
        assert Manor.objects.get(pk=manor.pk).grain == 1_100


@override_settings(GAME_TIME_MULTIPLIER=1, RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0)
@pytest.mark.parametrize("failure_stage", ["inventory", "event"])
def test_resource_reuse_rolls_back_complete_chunk_on_later_failure(monkeypatch, failure_stage):
    now = timezone.now()
    manors, grain_template = _world(2, now)
    manor_ids = [manor.pk for manor in manors]
    before = _ledger_snapshot(manor_ids)
    if failure_stage == "inventory":
        target = inventory_core
        function_name = "_set_warehouse_grain_quantity_on_locked_item"
    else:
        target = resources
        function_name = "log_resource_gain"
    original = getattr(target, function_name)
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        original(*args, **kwargs)
        if calls == 2:
            raise DatabaseError(f"second {failure_stage} write failed")

    monkeypatch.setattr(target, function_name, fail_second)
    with pytest.raises(DatabaseError, match=f"second {failure_stage} write failed"):
        resources.sync_resource_production_batch(
            manor_ids, grain_template=grain_template, grain_template_resolved=True, now=now
        )
    assert calls == 2
    assert _ledger_snapshot(manor_ids) == before


def test_locked_grain_writer_rejects_wrong_ownership_and_storage():
    now = timezone.now()
    manors, grain_template = _world(2, now)
    first, second = manors
    item = InventoryItem.objects.get(manor=first, template=grain_template)
    with pytest.raises(AssertionError, match="belong to the manor"):
        inventory_core._set_warehouse_grain_quantity_on_locked_item(second, 900, item, grain_template=grain_template)
    treasury = InventoryItem.objects.create(
        manor=first, template=grain_template, quantity=100, storage_location=InventoryItem.StorageLocation.TREASURY
    )
    with pytest.raises(AssertionError, match="belong to the manor"):
        inventory_core._set_warehouse_grain_quantity_on_locked_item(first, 900, treasury, grain_template=grain_template)


@pytest.mark.django_db(transaction=True)
def test_grain_reuse_helpers_reject_missing_transaction():
    now = timezone.now()
    manors, grain_template = _world(1, now)
    manor = manors[0]
    item = InventoryItem.objects.get(manor=manor, template=grain_template)
    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        inventory_core._load_warehouse_grain_ledger_locked(manor, grain_template=grain_template)
    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        inventory_core._set_warehouse_grain_quantity_on_locked_item(manor, 900, item, grain_template=grain_template)
