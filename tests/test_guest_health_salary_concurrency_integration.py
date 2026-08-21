from __future__ import annotations

import threading
import time

import pytest
from django.db import close_old_connections, connection, transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import NoGuestsError
from gameplay.models import Manor
from gameplay.services.manor.core import ensure_manor
from guests.constants import TimeConstants
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate, SalaryPayment
from guests.services import health as health_service
from guests.services import roster as roster_service
from guests.services import salary as salary_service

pytestmark = [pytest.mark.integration]


def _require_isolated_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("guest health and salary concurrency evidence requires MySQL row locks")
    if str(connection.settings_dict["NAME"]) != "test_webgame":
        pytest.skip("guest health and salary concurrency evidence only runs on test_webgame")


def _create_guest(django_user_model, *, username: str) -> tuple[Manor, Guest]:
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key=f"{username}_template",
        name="并发事务测试门客",
        rarity=GuestRarity.GRAY,
        base_hp=1_000,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        current_hp=1,
    )
    return manor, guest


@pytest.mark.django_db(transaction=True)
def test_locked_passive_recovery_preserves_concurrent_daily_loyalty_increment(
    django_user_model,
    monkeypatch,
) -> None:
    _require_isolated_mysql()
    manor, guest = _create_guest(
        django_user_model,
        username="guest_health_loyalty_row_lock",
    )
    now = timezone.now()
    Guest.objects.filter(pk=guest.pk).update(
        status=GuestStatus.INJURED,
        loyalty=10,
        current_hp=1,
        last_hp_recovery_at=(now - timezone.timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)),
        injury_loyalty_processed_at=now - timezone.timedelta(hours=3),
    )

    recovery_lock_held = threading.Event()
    release_recovery = threading.Event()
    loyalty_update_started = threading.Event()
    original_recover_guest_hp = health_service.recover_guest_hp
    recovery_results: list[bool] = []
    loyalty_update_counts: list[int] = []
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    def _recover_guest_hp_with_pause(locked_guest: Guest, *, now) -> None:
        recovery_lock_held.set()
        if not release_recovery.wait(timeout=10):
            raise TimeoutError("timed out waiting to release passive recovery")
        original_recover_guest_hp(locked_guest, now=now)

    monkeypatch.setattr(
        health_service,
        "recover_guest_hp",
        _recover_guest_hp_with_pause,
    )

    def _recovery_worker() -> None:
        close_old_connections()
        try:
            changed = health_service.recover_guest_hp_for_guest(guest.pk, now=now)
            with result_guard:
                recovery_results.append(changed)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _loyalty_worker() -> None:
        close_old_connections()
        try:
            loyalty_update_started.set()
            updated = Guest.objects.filter(pk=guest.pk).update(loyalty=F("loyalty") + 1)
            with result_guard:
                loyalty_update_counts.append(updated)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    recovery_thread = threading.Thread(target=_recovery_worker, daemon=True)
    loyalty_thread = threading.Thread(target=_loyalty_worker, daemon=True)
    recovery_thread.start()
    try:
        assert recovery_lock_held.wait(timeout=10)
        loyalty_thread.start()
        assert loyalty_update_started.wait(timeout=10)
        time.sleep(0.2)
        assert loyalty_thread.is_alive() is True
    finally:
        release_recovery.set()

    recovery_thread.join(timeout=20)
    loyalty_thread.join(timeout=20)
    assert recovery_thread.is_alive() is False
    assert loyalty_thread.is_alive() is False
    assert errors == []
    assert recovery_results == [True]
    assert loyalty_update_counts == [1]

    guest.refresh_from_db()
    assert guest.manor_id == manor.pk
    assert guest.current_hp > 1
    assert guest.loyalty == 10
    assert guest.injury_loyalty_processed_at == now


@pytest.mark.django_db(transaction=True)
def test_dismiss_guest_and_salary_follow_manor_then_guest_without_deadlock(
    django_user_model,
    monkeypatch,
) -> None:
    _require_isolated_mysql()
    manor, guest = _create_guest(
        django_user_model,
        username="guest_salary_roster_row_lock",
    )
    Manor.objects.filter(pk=manor.pk).update(silver=20_000)
    for_date = timezone.localdate()
    dismiss_locks_held = threading.Event()
    release_dismiss = threading.Event()
    salary_started = threading.Event()
    dismiss_results: list[roster_service.DismissGuestResult] = []
    salary_results: list[dict] = []
    dismiss_errors: list[BaseException] = []
    salary_errors: list[BaseException] = []
    result_guard = threading.Lock()
    original_guest_delete = Guest.delete

    def _delete_with_pause(self, *args, **kwargs):
        if threading.current_thread().name == "dismiss-guest-worker" and self.pk == guest.pk:
            dismiss_locks_held.set()
            if not release_dismiss.wait(timeout=10):
                raise TimeoutError("timed out waiting to release guest dismissal")
        return original_guest_delete(self, *args, **kwargs)

    monkeypatch.setattr(Guest, "delete", _delete_with_pause)

    def _dismiss_worker() -> None:
        close_old_connections()
        try:
            local_guest = Guest.objects.get(pk=guest.pk)
            result = roster_service.dismiss_guest(local_guest)
            with result_guard:
                dismiss_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                dismiss_errors.append(exc)
        finally:
            close_old_connections()

    def _salary_worker() -> None:
        close_old_connections()
        try:
            salary_started.set()
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                result = salary_service.pay_all_salaries_locked(
                    locked_manor,
                    for_date=for_date,
                )
            with result_guard:
                salary_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                salary_errors.append(exc)
        finally:
            close_old_connections()

    dismiss_thread = threading.Thread(
        target=_dismiss_worker,
        name="dismiss-guest-worker",
        daemon=True,
    )
    salary_thread = threading.Thread(target=_salary_worker, daemon=True)
    dismiss_thread.start()
    try:
        assert dismiss_locks_held.wait(timeout=10)
        salary_thread.start()
        assert salary_started.wait(timeout=10)
        time.sleep(0.2)
        assert salary_thread.is_alive() is True
    finally:
        release_dismiss.set()

    dismiss_thread.join(timeout=20)
    salary_thread.join(timeout=20)
    assert dismiss_thread.is_alive() is False
    assert salary_thread.is_alive() is False
    assert dismiss_errors == []
    assert len(dismiss_results) == 1
    assert dismiss_results[0].guest_name == guest.display_name
    assert salary_results == []
    assert len(salary_errors) == 1
    assert isinstance(salary_errors[0], NoGuestsError)

    manor.refresh_from_db(fields=["silver"])
    assert manor.silver == 20_000
    assert not Guest.objects.filter(pk=guest.pk).exists()
    assert not SalaryPayment.objects.filter(manor=manor, for_date=for_date).exists()


@pytest.mark.django_db(transaction=True)
def test_salary_manor_and_roster_locks_linearize_a_concurrent_guest_insert(
    django_user_model,
    monkeypatch,
) -> None:
    _require_isolated_mysql()
    manor, guest = _create_guest(
        django_user_model,
        username="guest_salary_insert_row_lock",
    )
    Manor.objects.filter(pk=manor.pk).update(silver=20_000)
    for_date = timezone.localdate()
    salary_locks_held = threading.Event()
    release_salary = threading.Event()
    insert_started = threading.Event()
    salary_results: list[dict] = []
    inserted_guest_ids: list[int] = []
    errors: list[BaseException] = []
    result_guard = threading.Lock()
    original_quote_salary_batch = salary_service._quote_salary_batch

    def _quote_with_pause(
        locked_guests: list[Guest],
        *,
        for_date,
        paid_guest_ids=None,
        salary_scale=salary_service.DEFAULT_SALARY_SCALE,
    ) -> salary_service.SalaryBatchQuote:
        salary_locks_held.set()
        if not release_salary.wait(timeout=10):
            raise TimeoutError("timed out waiting to release salary transaction")
        return original_quote_salary_batch(
            locked_guests,
            for_date=for_date,
            paid_guest_ids=paid_guest_ids,
            salary_scale=salary_scale,
        )

    monkeypatch.setattr(salary_service, "_quote_salary_batch", _quote_with_pause)

    def _salary_worker() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                result = salary_service.pay_all_salaries_locked(
                    locked_manor,
                    for_date=for_date,
                )
            with result_guard:
                salary_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _insert_worker() -> None:
        close_old_connections()
        try:
            insert_started.set()
            inserted_guest = Guest.objects.create(
                manor_id=manor.pk,
                template_id=guest.template_id,
                current_hp=1,
            )
            with result_guard:
                inserted_guest_ids.append(inserted_guest.pk)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    salary_thread = threading.Thread(target=_salary_worker, daemon=True)
    insert_thread = threading.Thread(target=_insert_worker, daemon=True)
    salary_thread.start()
    try:
        assert salary_locks_held.wait(timeout=10)
        insert_thread.start()
        assert insert_started.wait(timeout=10)
        time.sleep(0.2)
        assert insert_thread.is_alive() is True
    finally:
        release_salary.set()

    salary_thread.join(timeout=20)
    insert_thread.join(timeout=20)
    assert salary_thread.is_alive() is False
    assert insert_thread.is_alive() is False
    assert errors == []
    assert len(salary_results) == 1
    assert salary_results[0]["paid_count"] == 1
    assert len(inserted_guest_ids) == 1

    manor.refresh_from_db(fields=["silver"])
    assert manor.silver == 19_000
    assert Guest.objects.filter(manor=manor).count() == 2
    assert SalaryPayment.objects.filter(
        guest=guest,
        for_date=for_date,
        amount=1_000,
    ).exists()
    assert not SalaryPayment.objects.filter(
        guest_id=inserted_guest_ids[0],
        for_date=for_date,
    ).exists()
