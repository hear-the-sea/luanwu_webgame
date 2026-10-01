from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import TradeValidationError
from gameplay.models import Manor, PlayerTroop, TroopBankStorage, TroopRecruitment
from gameplay.services.manor.troop_bank import deposit_available_troops_to_bank, withdraw_available_troops_from_bank
from tests.test_troop_dispatch_concurrency_integration import _create_manor, _create_template, _run_concurrently

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def _require_mysql():
    if connection.vendor != "mysql":
        pytest.skip("bulk troop bank concurrency requires real MySQL row locks")


def _counts(model, manor):
    return dict(model.objects.filter(manor=manor).values_list("troop_template__key", "count"))


def _combined_counts(manor):
    combined = _counts(PlayerTroop, manor)
    for key, count in _counts(TroopBankStorage, manor).items():
        combined[key] = combined.get(key, 0) + count
    return combined


def _assert_one_legal_failure(successes, errors, *, message):
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], TradeValidationError)
    assert message in str(errors[0])


@pytest.mark.parametrize("deposit", [True, False], ids=["deposit", "withdraw"])
def test_duplicate_bulk_requests_cannot_transfer_same_inventory_twice(django_user_model, deposit):
    manor = _create_manor(django_user_model)
    first = _create_template()
    second = _create_template()
    source = PlayerTroop if deposit else TroopBankStorage
    target = TroopBankStorage if deposit else PlayerTroop
    action = deposit_available_troops_to_bank if deposit else withdraw_available_troops_from_bank
    source.objects.create(manor=manor, troop_template=first, count=60)
    source.objects.create(manor=manor, troop_template=second, count=35)
    target.objects.create(manor=manor, troop_template=first, count=5)
    before = _combined_counts(manor)
    results = []

    def transfer():
        results.append(action(Manor(pk=manor.pk)))

    successes, errors = _run_concurrently(transfer, transfer)

    _assert_one_legal_failure(successes, errors, message="暂无")
    assert results == [{"quantity": 95, "remaining": 0, "target_remaining": 4900}]
    assert sum(_counts(source, manor).values()) == 0
    assert _counts(target, manor) == {first.key: 65, second.key: 35}
    assert _combined_counts(manor) == before
    assert target.objects.filter(manor=manor).count() == 2


def _category_template(key):
    template, _ = TroopTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": "并发分类测试护院",
            "base_attack": 10,
            "base_defense": 10,
            "base_hp": 10,
            "default_count": 0,
        },
    )
    return template


@pytest.mark.parametrize("deposit", [True, False], ids=["deposit", "withdraw"])
def test_concurrent_categories_share_capacity_without_overflow(django_user_model, deposit):
    manor = _create_manor(django_user_model)
    blade = _category_template("dao_ke")
    archer = _category_template("archer")
    occupied = _create_template()
    source = PlayerTroop if deposit else TroopBankStorage
    target = TroopBankStorage if deposit else PlayerTroop
    action = deposit_available_troops_to_bank if deposit else withdraw_available_troops_from_bank
    source.objects.create(manor=manor, troop_template=blade, count=10)
    source.objects.create(manor=manor, troop_template=archer, count=20)
    target.objects.create(manor=manor, troop_template=occupied, count=4993)
    before = _combined_counts(manor)
    results = []

    def transfer(category):
        results.append((category, action(Manor(pk=manor.pk), category)))

    successes, errors = _run_concurrently(lambda: transfer("dao"), lambda: transfer("gong"))

    _assert_one_legal_failure(successes, errors, message="已满")
    assert len(results) == 1
    category, result = results[0]
    assert result == {"quantity": 7, "remaining": (10 if category == "dao" else 20) - 7, "target_remaining": 0}
    assert sum(_counts(target, manor).values()) == 5000
    assert sum(_counts(source, manor).values()) == 23
    assert _counts(target, manor)[occupied.key] == 4993
    assert _combined_counts(manor) == before
    winner_key, loser_key = (blade.key, archer.key) if category == "dao" else (archer.key, blade.key)
    assert _counts(target, manor)[winner_key] == 7
    assert loser_key not in _counts(target, manor)


def test_concurrent_bulk_withdrawals_leave_pending_recruitment_capacity_reserved(django_user_model):
    manor = _create_manor(django_user_model)
    template = _create_template()
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=4970)
    TroopBankStorage.objects.create(manor=manor, troop_template=template, count=50)
    recruitment = TroopRecruitment.objects.create(
        manor=manor,
        troop_key=template.key,
        troop_name=template.name,
        quantity=20,
        equipment_costs={},
        retainer_cost=20,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )
    before = _combined_counts(manor)
    results = []

    def withdraw():
        results.append(withdraw_available_troops_from_bank(Manor(pk=manor.pk)))

    successes, errors = _run_concurrently(withdraw, withdraw)

    _assert_one_legal_failure(successes, errors, message="募兵预留")
    assert results == [{"quantity": 10, "remaining": 40, "target_remaining": 0}]
    assert _counts(PlayerTroop, manor) == {template.key: 4980}
    assert _counts(TroopBankStorage, manor) == {template.key: 40}
    assert _combined_counts(manor) == before
    recruitment.refresh_from_db()
    assert recruitment.status == TroopRecruitment.Status.RECRUITING
    assert sum(_counts(PlayerTroop, manor).values()) + recruitment.quantity == 5000
