from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from threading import Barrier, Lock, Thread

import pytest
from django.db import close_old_connections, connection

from core.exceptions import GuestItemOwnershipError, GuestNotIdleError, GuestOwnershipError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus, RecruitmentPool
from guests.services.health import quote_medicine_item_for_guest, use_medicine_item_for_guest
from guests.services.recruitment import recruit_guest
from guests.services.recruitment_guests import finalize_candidate


def _bootstrap_injured_guest(game_data, django_user_model, *, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])

    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=1)[0]
    guest = finalize_candidate(candidate)

    injured_hp = max(1, int(guest.max_hp * 0.1))
    guest.current_hp = injured_hp
    guest.status = GuestStatus.INJURED
    guest.save(update_fields=["current_hp", "status"])
    return manor, guest


def _create_medicine_item(manor, *, heal_amount: int = 120) -> InventoryItem:
    suffix = uuid.uuid4().hex[:10]
    template = ItemTemplate.objects.create(
        key=f"test_medicine_item_{suffix}",
        name="疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        is_usable=True,
        tradeable=False,
        effect_payload={"hp": heal_amount},
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


@pytest.mark.django_db
def test_medicine_quote_is_read_only_immutable_and_uses_item_payload(
    game_data,
    django_user_model,
):
    manor, guest = _bootstrap_injured_guest(
        game_data,
        django_user_model,
        username="medicine_item_quote",
    )
    item = _create_medicine_item(manor, heal_amount=137)
    before = (guest.current_hp, guest.status, item.quantity)

    quote = quote_medicine_item_for_guest(manor, guest, item)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert quote.heal_amount == 137
    assert quote.healed == min(137, quote.max_hp - quote.current_hp_before)
    assert (guest.current_hp, guest.status, item.quantity) == before
    with pytest.raises(FrozenInstanceError):
        quote.heal_amount = 1  # type: ignore[misc]


@pytest.mark.django_db
def test_use_medicine_item_for_guest_is_atomic_success(game_data, django_user_model):
    manor, guest = _bootstrap_injured_guest(game_data, django_user_model, username="medicine_item_atomic_ok")
    heal_amount = max(1, int(guest.max_hp * 0.3))
    item = _create_medicine_item(manor, heal_amount=heal_amount)

    before_hp = guest.current_hp
    result = use_medicine_item_for_guest(manor, guest, item.pk, heal_amount)

    guest.refresh_from_db()
    assert guest.current_hp > before_hp
    assert guest.status == GuestStatus.IDLE
    assert bool(result["injury_cured"]) is True
    assert int(result["remaining_item_quantity"]) == 0
    assert InventoryItem.objects.filter(pk=item.pk).exists() is False


@pytest.mark.django_db
def test_use_medicine_item_for_guest_rolls_back_when_consume_fails(monkeypatch, game_data, django_user_model):
    manor, guest = _bootstrap_injured_guest(game_data, django_user_model, username="medicine_item_atomic_rollback")
    heal_amount = max(1, int(guest.max_hp * 0.25))
    item = _create_medicine_item(manor, heal_amount=heal_amount)

    before_hp = guest.current_hp
    before_status = guest.status

    def _boom(*_args, **_kwargs):
        raise RuntimeError("consume failed")

    monkeypatch.setattr("gameplay.services.inventory.core.consume_inventory_item_locked", _boom)

    with pytest.raises(RuntimeError, match="consume failed"):
        use_medicine_item_for_guest(manor, guest, item.pk, heal_amount)

    guest.refresh_from_db()
    item.refresh_from_db()

    assert guest.current_hp == before_hp
    assert guest.status == before_status
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_medicine_item_for_guest_rejects_malformed_heal_result(monkeypatch, game_data, django_user_model):
    manor, guest = _bootstrap_injured_guest(game_data, django_user_model, username="medicine_item_malformed_result")
    heal_amount = max(1, int(guest.max_hp * 0.2))
    item = _create_medicine_item(manor, heal_amount=heal_amount)

    def _malformed_heal(_guest, _heal_amount):
        return {"healed": "bad", "injury_cured": False}

    monkeypatch.setattr("guests.services.health.heal_guest", _malformed_heal)

    with pytest.raises(AssertionError, match="invalid medicine healed"):
        use_medicine_item_for_guest(manor, guest, item.pk, heal_amount)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert item.quantity == 1
    assert guest.status == GuestStatus.INJURED


@pytest.mark.django_db
def test_use_medicine_item_for_guest_rejects_non_idle_busy_status(game_data, django_user_model):
    manor, guest = _bootstrap_injured_guest(game_data, django_user_model, username="medicine_item_busy_reject")
    guest.status = GuestStatus.WORKING
    guest.save(update_fields=["status"])
    heal_amount = max(1, int(guest.max_hp * 0.2))
    item = _create_medicine_item(manor, heal_amount=heal_amount)

    with pytest.raises(GuestNotIdleError):
        use_medicine_item_for_guest(manor, guest, item.pk, heal_amount)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.status == GuestStatus.WORKING
    assert item.quantity == 1


@pytest.mark.django_db
def test_use_medicine_item_for_guest_rejects_missing_item(game_data, django_user_model):
    manor, guest = _bootstrap_injured_guest(game_data, django_user_model, username="medicine_item_missing_item")
    heal_amount = max(1, int(guest.max_hp * 0.2))

    with pytest.raises(GuestItemOwnershipError, match="道具不存在或不属于您的庄园"):
        use_medicine_item_for_guest(manor, guest, 999999, heal_amount)


@pytest.mark.django_db
def test_use_medicine_item_for_guest_rejects_guest_outside_manor(game_data, django_user_model):
    manor, _guest = _bootstrap_injured_guest(game_data, django_user_model, username="medicine_item_foreign_manor")
    other_manor, other_guest = _bootstrap_injured_guest(
        game_data, django_user_model, username="medicine_item_foreign_guest"
    )
    heal_amount = max(1, int(other_guest.max_hp * 0.2))
    item = _create_medicine_item(manor, heal_amount=heal_amount)

    with pytest.raises(GuestOwnershipError, match="门客不存在或不属于您的庄园"):
        use_medicine_item_for_guest(manor, other_guest, item.pk, heal_amount)

    item.refresh_from_db()
    assert item.quantity == 1
    assert other_guest.manor_id == other_manor.id


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_concurrent_medicine_use_consumes_at_most_one_item(
    game_data,
    django_user_model,
):
    if connection.vendor != "mysql":
        pytest.skip("medicine row-lock race requires MySQL")
    manor, guest = _bootstrap_injured_guest(
        game_data,
        django_user_model,
        username="medicine_item_concurrent",
    )
    item = _create_medicine_item(
        manor,
        heal_amount=max(1, int(guest.max_hp * 0.3)),
    )
    barrier = Barrier(2)
    result_lock = Lock()
    successes: list[dict[str, object]] = []
    failures: list[Exception] = []

    def _worker() -> None:
        close_old_connections()
        try:
            from gameplay.models import Manor
            from guests.models import Guest

            worker_manor = Manor.objects.get(pk=manor.pk)
            worker_guest = Guest.objects.get(pk=guest.pk)
            barrier.wait(timeout=10)
            result = use_medicine_item_for_guest(
                worker_manor,
                worker_guest,
                item.pk,
            )
            with result_lock:
                successes.append(result)
        except Exception as exc:
            with result_lock:
                failures.append(exc)
        finally:
            close_old_connections()

    workers = [Thread(target=_worker) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)

    assert all(not worker.is_alive() for worker in workers)
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], GuestItemOwnershipError)
    assert not InventoryItem.objects.filter(pk=item.pk).exists()
