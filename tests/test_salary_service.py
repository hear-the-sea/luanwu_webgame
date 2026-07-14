from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.exceptions import InsufficientResourceError, SalaryAlreadyPaidError
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestRarity, GuestTemplate, SalaryPayment
from guests.services.salary import pay_all_salaries, pay_guest_salary


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
