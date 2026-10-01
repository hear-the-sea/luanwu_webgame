from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.db.models import QuerySet
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import TradeValidationError
from gameplay.models import PlayerTroop, TroopBankStorage, TroopRecruitment
from gameplay.services.manor import troop_bank
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db


@pytest.fixture
def manor(django_user_model):
    user = django_user_model.objects.create_user(username="bulk_bank_player", password="pass12345")
    return ensure_manor(user)


def _template(key: str, *, priority: int = 0) -> TroopTemplate:
    template, _ = TroopTemplate.objects.update_or_create(
        key=key,
        defaults={
            "name": key,
            "description": "",
            "base_attack": 10,
            "base_defense": 10,
            "base_hp": 10,
            "speed_bonus": 0,
            "priority": priority,
            "default_count": 0,
        },
    )
    return template


def _counts(model, manor):
    return dict(model.objects.filter(manor=manor, count__gt=0).values_list("troop_template__key", "count"))


def _setup_transfer(manor, *, deposit: bool, source_count: int, target_count: int = 0):
    source = PlayerTroop if deposit else TroopBankStorage
    target = TroopBankStorage if deposit else PlayerTroop
    action = troop_bank.deposit_available_troops_to_bank if deposit else troop_bank.withdraw_available_troops_from_bank
    template = _template("bulk_transfer")
    if source_count:
        source.objects.create(manor=manor, troop_template=template, count=source_count)
    if target_count:
        target.objects.create(manor=manor, troop_template=template, count=target_count)
    return source, target, action


@pytest.mark.parametrize("deposit", [True, False])
def test_bulk_transfer_multiple_troops_preserves_counts(manor, deposit):
    source, target, action = _setup_transfer(manor, deposit=deposit, source_count=30, target_count=5)
    other = _template("bulk_other")
    source.objects.create(manor=manor, troop_template=other, count=20)

    result = action(manor)

    assert result == {"quantity": 50, "remaining": 0, "target_remaining": 4945}
    assert _counts(source, manor) == {}
    assert _counts(target, manor) == {"bulk_transfer": 35, "bulk_other": 20}
    with pytest.raises(TradeValidationError, match="暂无"):
        action(manor)
    assert _counts(target, manor) == {"bulk_transfer": 35, "bulk_other": 20}


@pytest.mark.parametrize("deposit", [True, False])
def test_bulk_partial_transfer_fills_capacity_in_priority_and_key_order(manor, deposit):
    source, target, action = _setup_transfer(manor, deposit=deposit, source_count=20, target_count=4985)
    first = _template("bulk_a", priority=-1)
    second = _template("bulk_b", priority=-1)
    source.objects.create(manor=manor, troop_template=second, count=10)
    source.objects.create(manor=manor, troop_template=first, count=10)

    result = action(manor)

    assert result == {"quantity": 15, "remaining": 25, "target_remaining": 0}
    assert _counts(source, manor) == {"bulk_transfer": 20, "bulk_b": 5}
    assert _counts(target, manor) == {"bulk_transfer": 4985, "bulk_a": 10, "bulk_b": 5}
    with pytest.raises(TradeValidationError, match="已满"):
        action(manor)
    assert _counts(source, manor) == {"bulk_transfer": 20, "bulk_b": 5}
    assert sum(_counts(target, manor).values()) == 5000


@pytest.mark.parametrize("deposit", [True, False])
@pytest.mark.parametrize("source_count,target_count,message", [(10, 5000, "已满"), (0, 0, "暂无")])
def test_bulk_full_or_empty_does_not_change_inventory(manor, deposit, source_count, target_count, message):
    source, target, action = _setup_transfer(
        manor, deposit=deposit, source_count=source_count, target_count=target_count
    )
    before = (_counts(source, manor), _counts(target, manor))

    with pytest.raises(TradeValidationError, match=message):
        action(manor)

    assert (_counts(source, manor), _counts(target, manor)) == before


@pytest.mark.parametrize("reserved,moved", [(5, 5), (10, 0)])
def test_bulk_withdraw_respects_pending_recruitment(manor, reserved, moved):
    source, target, action = _setup_transfer(manor, deposit=False, source_count=20, target_count=4990)
    TroopRecruitment.objects.create(
        manor=manor,
        troop_key="bulk_transfer",
        troop_name="bulk_transfer",
        quantity=reserved,
        equipment_costs={},
        retainer_cost=reserved,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )

    if moved:
        assert action(manor) == {"quantity": moved, "remaining": 20 - moved, "target_remaining": 0}
    else:
        with pytest.raises(TradeValidationError, match="募兵预留"):
            action(manor)
    assert _counts(source, manor) == {"bulk_transfer": 20 - moved}
    assert _counts(target, manor) == {"bulk_transfer": 4990 + moved}


@pytest.mark.parametrize("deposit", [True, False])
def test_bulk_category_filters_real_troop_classes_and_rejects_invalid(manor, deposit):
    source, target, action = _setup_transfer(manor, deposit=deposit, source_count=0)
    blade = _template("dao_ke")
    archer = _template("archer")
    source.objects.create(manor=manor, troop_template=blade, count=12)
    source.objects.create(manor=manor, troop_template=archer, count=8)

    with pytest.raises(TradeValidationError, match="有效"):
        action(manor, category="not_a_category")
    with pytest.raises(TradeValidationError, match="暂无"):
        action(manor, category="qiang")
    assert _counts(source, manor) == {"dao_ke": 12, "archer": 8}
    assert _counts(target, manor) == {}

    assert action(manor, category="dao") == {"quantity": 12, "remaining": 0, "target_remaining": 4988}
    assert _counts(source, manor) == {"archer": 8}
    assert _counts(target, manor) == {"dao_ke": 12}

    assert action(manor, category="gong") == {"quantity": 8, "remaining": 0, "target_remaining": 4980}
    assert _counts(source, manor) == {}
    assert _counts(target, manor) == {"dao_ke": 12, "archer": 8}


@pytest.mark.parametrize("deposit", [True, False])
def test_bulk_database_failure_mid_batch_rolls_back_all_transfers(manor, deposit, monkeypatch):
    source, target, action = _setup_transfer(manor, deposit=deposit, source_count=10)
    other = _template("bulk_other")
    source.objects.create(manor=manor, troop_template=other, count=20)
    before = (_counts(source, manor), _counts(target, manor))
    transfer_name = "deposit_troops_to_bank" if deposit else "withdraw_troops_from_bank"
    original = getattr(troop_bank, transfer_name)
    calls = []

    def fail_second_transfer(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise DatabaseError("second transfer failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(troop_bank, transfer_name, fail_second_transfer)
    with pytest.raises(DatabaseError, match="second transfer"):
        action(manor)

    assert len(calls) == 2
    assert (_counts(source, manor), _counts(target, manor)) == before


@pytest.mark.parametrize("deposit", [True, False])
def test_bulk_capacity_query_failure_is_not_swallowed(manor, deposit, monkeypatch):
    source, target, action = _setup_transfer(manor, deposit=deposit, source_count=10)
    before = (_counts(source, manor), _counts(target, manor))

    def fail_aggregate(*args, **kwargs):
        raise DatabaseError("capacity query failed")

    monkeypatch.setattr(QuerySet, "aggregate", fail_aggregate)
    with pytest.raises(DatabaseError, match="capacity query"):
        action(manor)
    assert (_counts(source, manor), _counts(target, manor)) == before


@pytest.mark.parametrize("deposit", [True, False])
def test_bulk_transfer_does_not_read_or_modify_other_players(manor, deposit, django_user_model):
    source, target, action = _setup_transfer(manor, deposit=deposit, source_count=10)
    other_user = django_user_model.objects.create_user(username="bulk_other_player", password="pass12345")
    other_manor = ensure_manor(other_user)
    template = _template("bulk_transfer")
    source.objects.create(manor=other_manor, troop_template=template, count=30)
    target.objects.create(manor=other_manor, troop_template=template, count=5000)

    assert action(manor) == {"quantity": 10, "remaining": 0, "target_remaining": 4990}
    assert _counts(source, other_manor) == {"bulk_transfer": 30}
    assert _counts(target, other_manor) == {"bulk_transfer": 5000}
