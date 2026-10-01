from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

import pytest
from django.db import close_old_connections, connection, transaction

from battle.models import TroopTemplate
from core.exceptions import TroopLoadoutError
from gameplay.models import Manor, PlayerTroop, TroopBankStorage
from gameplay.services.manor.core import ensure_manor
from gameplay.services.manor.troop_bank import deposit_troops_to_bank, withdraw_troops_from_bank
from gameplay.services.manor.troop_dispatch import deduct_dispatch_troops_locked

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def _require_mysql():
    if connection.vendor != "mysql":
        pytest.skip("troop dispatch concurrency requires real MySQL row locks")


def _create_manor(django_user_model) -> Manor:
    user = django_user_model.objects.create_user(
        username=f"troop_dispatch_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    return ensure_manor(user)


def _create_template() -> TroopTemplate:
    return TroopTemplate.objects.create(
        key=f"troop_dispatch_concurrent_{uuid.uuid4().hex[:8]}",
        name="并发出征测试护院",
        base_attack=10,
        base_defense=10,
        base_hp=10,
        default_count=0,
    )


def _dispatch(manor_id: int, loadout: dict[str, int]) -> None:
    with transaction.atomic():
        manor = Manor.objects.select_for_update().get(pk=manor_id)
        deduct_dispatch_troops_locked(manor, loadout)


def _run_concurrently(*operations: Callable[[], None]) -> tuple[list[int], list[Exception]]:
    start = threading.Barrier(len(operations))
    successes: list[int] = []
    errors: list[Exception] = []

    def _worker(index: int, operation: Callable[[], None]) -> None:
        close_old_connections()
        try:
            # 故障场景也必须及时结束，避免失败线程占用测试数据库。
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
            start.wait(timeout=10)
            operation()
            successes.append(index)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            connection.close()
            close_old_connections()

    threads = [
        threading.Thread(target=_worker, args=(index, operation), daemon=True)
        for index, operation in enumerate(operations)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads), "concurrent troop operation did not finish"
    return successes, errors


def test_opposite_manor_and_bank_templates_do_not_lock_shared_templates(django_user_model):
    first_manor = _create_manor(django_user_model)
    second_manor = _create_manor(django_user_model)
    first_template = _create_template()
    second_template = _create_template()
    for manor, manor_template, bank_template in (
        (first_manor, first_template, second_template),
        (second_manor, second_template, first_template),
    ):
        PlayerTroop.objects.create(manor=manor, troop_template=manor_template, count=15)
        TroopBankStorage.objects.create(manor=manor, troop_template=bank_template, count=15)

    manor_rows_locked = threading.Barrier(2)
    loadout = {first_template.key: 10, second_template.key: 10}

    def _dispatch_after_both_manor_queries(manor_id: int) -> None:
        def _wait_after_manor_query(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (
                sql.lstrip().upper().startswith("SELECT")
                and "FOR UPDATE" in sql.upper()
                and "`gameplay_playertroop`" in sql
            ):
                # 两方分别持有庄园护院锁后再查询钱庄；模板若被连带锁定会形成死锁。
                manor_rows_locked.wait(timeout=10)
            return result

        with connection.execute_wrapper(_wait_after_manor_query):
            _dispatch(manor_id, loadout)

    successes, errors = _run_concurrently(
        lambda: _dispatch_after_both_manor_queries(first_manor.pk),
        lambda: _dispatch_after_both_manor_queries(second_manor.pk),
    )

    assert errors == []
    assert len(successes) == 2
    for manor in (first_manor, second_manor):
        assert list(PlayerTroop.objects.filter(manor=manor).values_list("count", flat=True)) == [5]
        assert list(TroopBankStorage.objects.filter(manor=manor).values_list("count", flat=True)) == [5]


def test_concurrent_dispatches_cannot_double_spend_bank_troops(django_user_model):
    manor = _create_manor(django_user_model)
    template = _create_template()
    bank_troop = TroopBankStorage.objects.create(manor=manor, troop_template=template, count=100)

    successes, errors = _run_concurrently(
        lambda: _dispatch(manor.pk, {template.key: 65}),
        lambda: _dispatch(manor.pk, {template.key: 65}),
    )

    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], TroopLoadoutError)
    assert "数量不足" in str(errors[0])
    bank_troop.refresh_from_db()
    assert bank_troop.count == 35
    assert not PlayerTroop.objects.filter(manor=manor).exists()
    assert TroopBankStorage.objects.filter(manor=manor, troop_template=template).count() == 1


@pytest.mark.parametrize(
    ("transfer", "expected_manor", "expected_bank"),
    [(deposit_troops_to_bank, 10, 90), (withdraw_troops_from_bank, 30, 70)],
    ids=["deposit", "withdraw"],
)
def test_dispatch_and_manual_bank_transfer_preserve_total(django_user_model, transfer, expected_manor, expected_bank):
    manor = _create_manor(django_user_model)
    template = _create_template()
    player_troop = PlayerTroop.objects.create(manor=manor, troop_template=template, count=80)
    bank_troop = TroopBankStorage.objects.create(manor=manor, troop_template=template, count=80)

    successes, errors = _run_concurrently(
        lambda: _dispatch(manor.pk, {template.key: 60}),
        lambda: transfer(Manor(pk=manor.pk), template.key, 10),
    )

    assert errors == []
    assert len(successes) == 2
    player_troop.refresh_from_db()
    bank_troop.refresh_from_db()
    assert player_troop.count == expected_manor
    assert bank_troop.count == expected_bank
    assert player_troop.count + bank_troop.count + 60 == 160
    assert PlayerTroop.objects.filter(manor=manor, troop_template=template).count() == 1
    assert TroopBankStorage.objects.filter(manor=manor, troop_template=template).count() == 1
