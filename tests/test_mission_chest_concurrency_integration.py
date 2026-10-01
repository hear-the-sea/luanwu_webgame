from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml
from django.db import connection, connections

from core.exceptions import InsufficientStockError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_same_mission_chest_can_only_be_opened_once_concurrently(django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("This test requires real row locks")
    manor = ensure_manor(django_user_model.objects.create_user(username="mission_chest_concurrent"))
    reward = ItemTemplate.objects.create(key="mission_chest_concurrent_reward", name="并发奖励")
    box = ItemTemplate.objects.create(
        key="mission_chest_concurrent",
        name="并发宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "mission_reward_version": 1,
            "item_rewards": [{"item_key": reward.key, "min_quantity": 3}],
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1)
    barrier = threading.Barrier(2)
    successes, expected_errors, unexpected_errors = [], [], []

    def worker():
        try:
            local_item = InventoryItem.objects.select_related("template").get(pk=item.pk)
            owner = Manor.objects.get(pk=manor.pk)
            barrier.wait(timeout=10)
            successes.append(use_inventory_item(local_item, owner))
        except (ItemNotFoundError, InsufficientStockError) as exc:
            expected_errors.append(exc)
        except Exception as exc:
            unexpected_errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert not unexpected_errors
    assert len(successes) == len(expected_errors) == 1
    assert InventoryItem.objects.get(manor=manor, template=reward).quantity == 3
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_collection_threshold_can_only_be_redeemed_once_concurrently(django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("This test requires real row locks")
    config = yaml.safe_load((Path(__file__).resolve().parents[1] / "data" / "item_templates.yaml").read_text())
    token = next(x for x in config["items"] if x["key"] == "zhuxian_sword_intent")
    payload = token["effect_payload"]
    manor = ensure_manor(django_user_model.objects.create_user(username="collection_concurrent"))
    reward = ItemTemplate.objects.create(key=payload["result_item_key"], name="诛仙剑")
    template = ItemTemplate.objects.create(
        key=token["key"], name=token["name"], effect_type="tool", is_usable=True, effect_payload=payload
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=payload["collection_cost"])
    barrier = threading.Barrier(2)
    successes, expected_errors, unexpected_errors = [], [], []

    def worker():
        try:
            local_item = InventoryItem.objects.select_related("template").get(pk=item.pk)
            owner = Manor.objects.get(pk=manor.pk)
            barrier.wait(timeout=10)
            successes.append(use_inventory_item(local_item, owner))
        except (ItemNotFoundError, InsufficientStockError) as exc:
            expected_errors.append(exc)
        except Exception as exc:
            unexpected_errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert not unexpected_errors
    assert len(successes) == len(expected_errors) == 1
    assert InventoryItem.objects.get(manor=manor, template=reward).quantity == 1
    assert not InventoryItem.objects.filter(pk=item.pk).exists()
