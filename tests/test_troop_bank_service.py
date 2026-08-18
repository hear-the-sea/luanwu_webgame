from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import TradeValidationError
from gameplay.models import PlayerTroop, TroopBankStorage, TroopRecruitment
from gameplay.services.manor.core import ensure_manor
from gameplay.services.manor.troop_bank import (
    TROOP_BANK_CAPACITY,
    deposit_troops_to_bank,
    get_troop_bank_remaining_space,
    get_troop_bank_rows,
    get_troop_bank_used_space,
    withdraw_troops_from_bank,
)
from gameplay.services.manor.troop_capacity import get_manor_troop_used_space


def _create_troop_template(key: str, name: str) -> TroopTemplate:
    template, _ = TroopTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": name,
            "description": "",
            "base_attack": 10,
            "base_defense": 10,
            "base_hp": 10,
            "speed_bonus": 0,
            "priority": 0,
            "default_count": 0,
        },
    )
    return template


@pytest.mark.django_db
def test_deposit_troops_to_bank_success(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_deposit", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_spear", "钱庄枪兵")
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=100)

    result = deposit_troops_to_bank(manor, template.key, 40)

    assert result["troop_name"] == template.name
    assert result["quantity"] == 40
    assert result["used"] == 40
    assert get_troop_bank_used_space(manor) == 40
    assert get_troop_bank_remaining_space(manor) == TROOP_BANK_CAPACITY - 40

    player_troop = PlayerTroop.objects.get(manor=manor, troop_template=template)
    bank_troop = TroopBankStorage.objects.get(manor=manor, troop_template=template)
    assert player_troop.count == 60
    assert bank_troop.count == 40

    rows = get_troop_bank_rows(manor)
    assert rows[0]["player_count"] == 60
    assert rows[0]["bank_count"] == 40


@pytest.mark.django_db
def test_deposit_troops_to_bank_rejects_capacity_overflow(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_capacity", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_blade", "钱庄刀手")
    other_template = _create_troop_template("bank_blade_other", "钱庄预占护院")

    PlayerTroop.objects.create(manor=manor, troop_template=template, count=100)
    TroopBankStorage.objects.create(manor=manor, troop_template=other_template, count=TROOP_BANK_CAPACITY - 10)

    with pytest.raises(TradeValidationError, match="容量不足"):
        deposit_troops_to_bank(manor, template.key, 11)

    # 上限边界可存入
    deposit_troops_to_bank(manor, template.key, 10)
    assert get_troop_bank_used_space(manor) == TROOP_BANK_CAPACITY


@pytest.mark.django_db
def test_withdraw_troops_from_bank_success(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_withdraw", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_archer", "钱庄弓兵")

    PlayerTroop.objects.create(manor=manor, troop_template=template, count=5)
    TroopBankStorage.objects.create(manor=manor, troop_template=template, count=30)

    result = withdraw_troops_from_bank(manor, template.key, 20)

    assert result["troop_name"] == template.name
    assert result["quantity"] == 20

    player_troop = PlayerTroop.objects.get(manor=manor, troop_template=template)
    bank_troop = TroopBankStorage.objects.get(manor=manor, troop_template=template)
    assert player_troop.count == 25
    assert bank_troop.count == 10
    assert result["manor_used"] == 25
    assert result["manor_capacity"] == 5000


@pytest.mark.django_db
def test_withdraw_troops_from_bank_rejects_manor_capacity_overflow(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_manor_capacity", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_capacity_boundary", "钱庄容量边界护院")

    PlayerTroop.objects.create(manor=manor, troop_template=template, count=4995)
    TroopBankStorage.objects.create(manor=manor, troop_template=template, count=10)

    with pytest.raises(TradeValidationError, match="庄园内护院容量不足"):
        withdraw_troops_from_bank(manor, template.key, 6)

    player_troop = PlayerTroop.objects.get(manor=manor, troop_template=template)
    bank_troop = TroopBankStorage.objects.get(manor=manor, troop_template=template)
    assert player_troop.count == 4995
    assert bank_troop.count == 10
    assert get_manor_troop_used_space(manor) == 4995

    result = withdraw_troops_from_bank(manor, template.key, 5)
    player_troop.refresh_from_db()
    bank_troop.refresh_from_db()
    assert player_troop.count == 5000
    assert bank_troop.count == 5
    assert result["manor_used"] == 5000


@pytest.mark.django_db
def test_withdraw_troops_from_bank_respects_pending_recruitment_reservation(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_pending_reserve", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_pending_reserve", "钱庄预留护院")

    PlayerTroop.objects.create(manor=manor, troop_template=template, count=4990)
    TroopBankStorage.objects.create(manor=manor, troop_template=template, count=10)
    TroopRecruitment.objects.create(
        manor=manor,
        troop_key=template.key,
        troop_name=template.name,
        quantity=10,
        equipment_costs={},
        retainer_cost=10,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )

    with pytest.raises(TradeValidationError, match="庄园内护院容量不足"):
        withdraw_troops_from_bank(manor, template.key, 1)

    assert PlayerTroop.objects.get(manor=manor, troop_template=template).count == 4990
    assert TroopBankStorage.objects.get(manor=manor, troop_template=template).count == 10


@pytest.mark.django_db
def test_withdraw_troops_from_bank_rejects_insufficient_count(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_insufficient", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_fist", "钱庄拳师")
    TroopBankStorage.objects.create(manor=manor, troop_template=template, count=8)

    with pytest.raises(TradeValidationError, match="数量不足"):
        withdraw_troops_from_bank(manor, template.key, 9)


@pytest.mark.django_db
def test_deposit_troops_to_bank_rejects_invalid_quantity(django_user_model):
    user = django_user_model.objects.create_user(username="troop_bank_invalid_qty", password="pass12345")
    manor = ensure_manor(user)
    template = _create_troop_template("bank_invalid_qty", "钱庄异常数量")
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=10)

    with pytest.raises(TradeValidationError, match="正整数"):
        deposit_troops_to_bank(manor, template.key, "bad")
