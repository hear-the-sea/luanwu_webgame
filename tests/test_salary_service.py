from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.exceptions import InsufficientResourceError, SalaryAlreadyPaidError
from gameplay.models import ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestRarity, GuestTemplate, SalaryPayment
from guests.services.salary import (
    get_guest_salary,
    pay_all_salaries,
    pay_all_salaries_locked,
    pay_guest_salary,
    quote_all_salaries,
)


@pytest.mark.django_db
def test_pay_guest_salary_defaults_to_shanghai_business_date():
    user = get_user_model().objects.create_user(username="salary_local_date_user", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 10_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_local_date_guest",
        name="本地日期工资门客",
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=template)
    utc_now = datetime(2026, 7, 13, 16, 30, tzinfo=UTC)

    with timezone.override(ZoneInfo("Asia/Shanghai")), patch("django.utils.timezone.now", return_value=utc_now):
        payment = pay_guest_salary(manor, guest)

    assert payment.for_date == date(2026, 7, 14)


@pytest.mark.django_db
def test_pay_guest_salary_creates_payment_and_deducts_silver():
    user = get_user_model().objects.create_user(username="salary_user", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 10_000
    manor.save(update_fields=["silver"])

    template = GuestTemplate.objects.create(
        key="salary_guest_tpl",
        name="工资测试门客",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    guest = Guest.objects.create(manor=manor, template=template, force=10, intellect=10)

    for_date = date(2026, 2, 7)
    payment = pay_guest_salary(manor, guest, for_date=for_date)

    assert SalaryPayment.objects.filter(pk=payment.pk).exists()
    salary_event = ResourceEvent.objects.get(
        manor=manor,
        reason=ResourceEvent.Reason.SALARY_COST,
    )
    assert salary_event.resource_type == ResourceType.SILVER
    assert salary_event.delta == -get_guest_salary(guest)
    manor.refresh_from_db(fields=["silver"])
    assert manor.silver < 10_000

    with pytest.raises(SalaryAlreadyPaidError):
        pay_guest_salary(manor, guest, for_date=for_date)


@pytest.mark.django_db
def test_pay_all_salaries_pays_only_unpaid_and_deducts_total():
    user = get_user_model().objects.create_user(username="salary_user2", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 50_000
    manor.save(update_fields=["silver"])

    template = GuestTemplate.objects.create(
        key="salary_guest_tpl2",
        name="工资测试门客2",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    g1 = Guest.objects.create(manor=manor, template=template, force=10, intellect=10)
    g2 = Guest.objects.create(manor=manor, template=template, force=10, intellect=10)

    for_date = date(2026, 2, 7)
    pay_guest_salary(manor, g1, for_date=for_date)

    before = manor.silver
    result = pay_all_salaries(manor, for_date=for_date)

    assert result["paid_count"] == 1
    assert set(result["guest_names"]).issubset({g1.display_name, g2.display_name})

    manor.refresh_from_db(fields=["silver"])
    assert manor.silver < before
    assert SalaryPayment.objects.filter(manor=manor, for_date=for_date).count() == 2
    assert ResourceEvent.objects.filter(manor=manor, reason=ResourceEvent.Reason.SALARY_COST).aggregate(
        total=Sum("delta")
    )["total"] == -(get_guest_salary(g1) + get_guest_salary(g2))


@pytest.mark.django_db
def test_salary_resource_event_survives_guest_replacement_delete():
    user = get_user_model().objects.create_user(username="salary_ledger_retention", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 10_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_ledger_retention_template",
        name="工资流水留存测试门客",
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=template)
    for_date = date(2026, 2, 12)

    payment = pay_guest_salary(manor, guest, for_date=for_date)
    event = ResourceEvent.objects.get(manor=manor, reason=ResourceEvent.Reason.SALARY_COST)
    expected_delta = -get_guest_salary(guest)

    guest.delete()

    assert not SalaryPayment.objects.filter(pk=payment.pk).exists()
    assert ResourceEvent.objects.filter(pk=event.pk).values_list("delta", flat=True).get() == expected_delta


@pytest.mark.django_db
def test_pay_all_salaries_insufficient_silver_raises():
    user = get_user_model().objects.create_user(username="salary_user3", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 0
    manor.save(update_fields=["silver"])

    template = GuestTemplate.objects.create(
        key="salary_guest_tpl3",
        name="工资测试门客3",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    Guest.objects.create(manor=manor, template=template, force=10, intellect=10)

    with pytest.raises(InsufficientResourceError):
        pay_all_salaries(manor, for_date=date(2026, 2, 7))


@pytest.mark.django_db(transaction=True)
def test_pay_all_salaries_locked_requires_an_outer_transaction():
    user = get_user_model().objects.create_user(
        username="salary_locked_requires_transaction",
        password="pass123",
    )
    manor = ensure_manor(user)

    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        pay_all_salaries_locked(manor, for_date=date(2026, 2, 7))


@pytest.mark.django_db
def test_pay_all_salaries_locked_rolls_back_balance_and_payments_together():
    user = get_user_model().objects.create_user(
        username="salary_locked_rollback",
        password="pass123",
    )
    manor = ensure_manor(user)
    manor.silver = 10_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_locked_rollback_guest",
        name="工资回滚测试门客",
        rarity=GuestRarity.GRAY,
    )
    Guest.objects.create(manor=manor, template=template)
    for_date = date(2026, 2, 7)

    with pytest.raises(RuntimeError, match="force rollback"):
        with transaction.atomic():
            locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
            pay_all_salaries_locked(locked_manor, for_date=for_date)
            raise RuntimeError("force rollback")

    manor.refresh_from_db(fields=["silver"])
    assert manor.silver == 10_000
    assert not SalaryPayment.objects.filter(manor=manor, for_date=for_date).exists()


@pytest.mark.django_db
def test_pay_all_salaries_locked_rejects_a_stale_expected_roster():
    user = get_user_model().objects.create_user(
        username="salary_locked_stale_roster",
        password="pass123",
    )
    manor = ensure_manor(user)
    manor.silver = 20_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_locked_stale_roster_guest",
        name="工资名单变化测试门客",
        rarity=GuestRarity.GRAY,
    )
    expected_guest = Guest.objects.create(manor=manor, template=template)
    expected_guests = [expected_guest]
    Guest.objects.create(manor=manor, template=template)
    for_date = date(2026, 2, 8)

    with transaction.atomic():
        locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
        with pytest.raises(ValueError, match="roster"):
            pay_all_salaries_locked(
                locked_manor,
                for_date=for_date,
                _guests=expected_guests,
            )

    manor.refresh_from_db(fields=["silver"])
    assert manor.silver == 20_000
    assert not SalaryPayment.objects.filter(manor=manor, for_date=for_date).exists()


@pytest.mark.django_db
def test_pay_all_salaries_locked_rejects_a_stale_frozen_quote():
    user = get_user_model().objects.create_user(
        username="salary_locked_stale_quote",
        password="pass123",
    )
    manor = ensure_manor(user)
    manor.silver = 20_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_locked_stale_quote_guest",
        name="工资报价变化测试门客",
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=template)
    for_date = date(2026, 2, 9)
    quote = quote_all_salaries(manor, for_date=for_date)
    SalaryPayment.objects.create(
        manor=manor,
        guest=guest,
        amount=get_guest_salary(guest),
        for_date=for_date,
    )

    with transaction.atomic():
        locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
        with pytest.raises(ValueError, match="locked salary state"):
            pay_all_salaries_locked(
                locked_manor,
                for_date=for_date,
                _quote=quote,
            )

    manor.refresh_from_db(fields=["silver"])
    assert manor.silver == 20_000
    assert SalaryPayment.objects.filter(manor=manor, for_date=for_date).count() == 1


@pytest.mark.django_db
def test_pay_all_salaries_locked_recomputes_amounts_from_locked_guests():
    user = get_user_model().objects.create_user(
        username="salary_locked_fresh_guest_state",
        password="pass123",
    )
    manor = ensure_manor(user)
    manor.silver = 20_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_locked_fresh_guest_state_template",
        name="工资锁内重算测试门客",
        rarity=GuestRarity.GRAY,
    )
    stale_guest = Guest.objects.create(manor=manor, template=template)
    GuestTemplate.objects.filter(pk=template.pk).update(rarity=GuestRarity.BLUE)
    for_date = date(2026, 2, 10)

    with transaction.atomic():
        locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
        result = pay_all_salaries_locked(
            locked_manor,
            for_date=for_date,
            _guests=[stale_guest],
        )

    payment = SalaryPayment.objects.get(guest=stale_guest, for_date=for_date)
    assert result["total_amount"] == 4_000
    assert payment.amount == 4_000
    manor.refresh_from_db(fields=["silver"])
    assert manor.silver == 16_000


@pytest.mark.django_db
def test_pay_all_salaries_locked_reuses_a_prelocked_roster_and_quote():
    user = get_user_model().objects.create_user(
        username="salary_locked_reuses_quote",
        password="pass123",
    )
    manor = ensure_manor(user)
    manor.silver = 20_000
    manor.save(update_fields=["silver"])
    template = GuestTemplate.objects.create(
        key="salary_locked_reuses_quote_guest",
        name="工资锁内复用测试门客",
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(manor=manor, template=template)
    for_date = date(2026, 2, 11)

    with transaction.atomic():
        locked_manor = type(manor).objects.select_for_update().get(pk=manor.pk)
        locked_guests = list(
            Guest.objects.select_for_update().select_related("template").filter(manor_id=locked_manor.id).order_by("id")
        )
        quote = quote_all_salaries(
            locked_manor,
            for_date=for_date,
            guests=locked_guests,
        )
        with (
            patch(
                "guests.services.salary._load_locked_salary_roster",
                side_effect=AssertionError("locked roster must be reused"),
            ),
            patch(
                "guests.services.salary.bulk_check_salary_paid",
                side_effect=AssertionError("locked quote must be reused"),
            ),
        ):
            result = pay_all_salaries_locked(
                locked_manor,
                for_date=for_date,
                _quote=quote,
                _locked_guests=locked_guests,
            )

    payment = SalaryPayment.objects.get(guest=guest, for_date=for_date)
    assert result == {
        "paid_count": 1,
        "total_amount": get_guest_salary(guest),
        "guest_names": [guest.display_name],
    }
    assert payment.amount == get_guest_salary(guest)
