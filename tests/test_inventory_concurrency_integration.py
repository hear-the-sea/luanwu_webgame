from __future__ import annotations

import threading
import uuid

import pytest
from django.db import close_old_connections, connection

from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.core import add_item_to_inventory
from gameplay.services.manor.core import ensure_manor

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_inventory_grants_keep_one_row_and_both_quantities(django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("inventory concurrency requires row-level select_for_update semantics")

    user = django_user_model.objects.create_user(
        username=f"inventory_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    manor = ensure_manor(user)
    item_template = ItemTemplate.objects.create(
        key=f"inventory_concurrent_item_{uuid.uuid4().hex[:8]}",
        name="并发库存测试道具",
        storage_space=1,
    )
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def _grant_worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            add_item_to_inventory(Manor(pk=manor.pk), item_template.key, 1)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_grant_worker), threading.Thread(target=_grant_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    rows = InventoryItem.objects.filter(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert rows.count() == 1
    assert rows.get().quantity == 2
