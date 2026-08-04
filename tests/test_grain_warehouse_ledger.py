from importlib import import_module
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.core import add_item_to_inventory, consume_inventory_item, get_warehouse_grain_quantity
from gameplay.services.manor.core import ensure_manor
from gameplay.services.resources import project_resource_production_for_read, sync_resource_production
from tests.gameplay_services.support import User, ensure_grain_template


@pytest.mark.django_db
def test_grain_inventory_mutations_use_the_warehouse_row_as_the_ledger():
    user = User.objects.create_user(username="grain_ledger_mutation", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 900
    manor.save(update_fields=["grain"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 400},
    )

    add_item_to_inventory(manor, "grain", 25)
    consume_inventory_item(manor, "grain", 75)

    manor.refresh_from_db(fields=["grain"])
    warehouse_grain = InventoryItem.objects.get(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert manor.grain == 350
    assert warehouse_grain.quantity == 350


@pytest.mark.django_db
def test_grain_read_projection_uses_warehouse_quantity_without_replacing_persisted_ledger(monkeypatch):
    user = User.objects.create_user(username="grain_ledger_read", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 999
    manor.resource_updated_at = timezone.now() - timezone.timedelta(hours=1)
    manor.save(update_fields=["grain", "resource_updated_at"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 777},
    )

    monkeypatch.setattr(
        "gameplay.services.resources.get_hourly_rates",
        lambda _manor: {"silver": 0, "grain": 120},
    )
    monkeypatch.setattr(
        "gameplay.services.resources.get_personnel_grain_cost_per_hour",
        lambda _manor: 0,
    )
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)

    project_resource_production_for_read(manor)

    assert manor.grain == 897
    manor.refresh_from_db(fields=["grain"])
    assert manor.grain == 999
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 777
    )


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=60)
def test_persisted_resource_sync_clears_stale_grain_projection() -> None:
    user = User.objects.create_user(username="grain_ledger_projection_reset", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    now = timezone.now()
    manor.grain = 777
    manor.resource_updated_at = now
    manor.save(update_fields=["grain", "resource_updated_at"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 777},
    )

    manor.grain = 999
    manor.warehouse_grain_quantity = 999
    sync_resource_production(manor, persist=True)

    assert manor.grain == 777
    assert not hasattr(manor, "warehouse_grain_quantity")
    assert get_warehouse_grain_quantity(manor) == 777


@pytest.mark.django_db
def test_repair_grain_warehouse_ledger_calibrates_existing_row():
    user = User.objects.create_user(username="grain_ledger_repair", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 999
    manor.save(update_fields=["grain"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 777},
    )

    call_command("repair_grain_warehouse_ledger", stdout=StringIO())

    manor.refresh_from_db(fields=["grain"])
    assert manor.grain == 999
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 999
    )


@pytest.mark.django_db
def test_repair_grain_warehouse_ledger_preserves_consistent_row() -> None:
    user = User.objects.create_user(username="grain_ledger_repair_consistent", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 999
    manor.save(update_fields=["grain"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 999},
    )

    stdout = StringIO()
    call_command("repair_grain_warehouse_ledger", stdout=stdout)

    assert "保留 1" in stdout.getvalue()
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 999
    )


@pytest.mark.django_db
def test_backfill_grain_warehouse_ledger_migration_calibrates_existing_row() -> None:
    user = User.objects.create_user(username="grain_ledger_migration_repair", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 999
    manor.save(update_fields=["grain"])
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 777},
    )

    migration_module = import_module("gameplay.migrations.0147_backfill_grain_warehouse_ledger")

    class _Apps:
        @staticmethod
        def get_model(app_label: str, model_name: str):
            assert app_label == "gameplay"
            if model_name == "Manor":
                return Manor
            if model_name == "InventoryItem":
                return InventoryItem
            if model_name == "ItemTemplate":
                return ItemTemplate
            raise LookupError(model_name)

    migration_module.backfill_grain_warehouse_ledger(_Apps(), None)

    manor.refresh_from_db(fields=["grain"])
    assert manor.grain == 999
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 999
    )


@pytest.mark.django_db
def test_backfill_grain_warehouse_ledger_migration_creates_missing_row() -> None:
    user = User.objects.create_user(username="grain_ledger_migration_missing", password="pass123")
    manor = ensure_manor(user)
    grain_template = ensure_grain_template()
    manor.grain = 777
    manor.save(update_fields=["grain"])

    migration_module = import_module("gameplay.migrations.0147_backfill_grain_warehouse_ledger")

    class _Apps:
        @staticmethod
        def get_model(app_label: str, model_name: str):
            assert app_label == "gameplay"
            if model_name == "Manor":
                return Manor
            if model_name == "InventoryItem":
                return InventoryItem
            if model_name == "ItemTemplate":
                return ItemTemplate
            raise LookupError(model_name)

    migration_module.backfill_grain_warehouse_ledger(_Apps(), None)

    row = InventoryItem.objects.get(
        manor=manor,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert row.quantity == 777
    assert row.created_at is not None
    assert row.updated_at is not None
