from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor
from guests import tasks as guest_tasks
from guests.models import Guest, GuestRarity, GuestTemplate, SalaryPayment
from guests.services.loyalty import extract_guest_ids, grant_battle_victory_loyalty, increase_guest_loyalty_by_ids


def test_extract_guest_ids_deduplicates_ids():
    guests = [
        SimpleNamespace(pk=1),
        SimpleNamespace(id=1),
        SimpleNamespace(id="2"),
    ]

    assert extract_guest_ids(guests) == [1, 2]


def test_extract_guest_ids_rejects_invalid_ids():
    guests = [
        SimpleNamespace(pk=1),
        SimpleNamespace(pk="bad"),
    ]

    with pytest.raises(AssertionError, match="invalid guest loyalty guest id"):
        extract_guest_ids(guests)


@pytest.mark.django_db
def test_grant_battle_victory_loyalty_caps_at_100(django_user_model):
    user = django_user_model.objects.create_user(username="loyalty_cap", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="loyalty_cap_tpl",
        name="忠诚测试门客",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    guest = Guest.objects.create(manor=manor, template=template, force=10, intellect=10, loyalty=100)

    updated = grant_battle_victory_loyalty([guest])

    guest.refresh_from_db(fields=["loyalty"])
    assert updated == 1
    assert guest.loyalty == 100


@pytest.mark.django_db
def test_increase_guest_loyalty_by_ids_rejects_invalid_amount(django_user_model):
    user = django_user_model.objects.create_user(username="loyalty_amount", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="loyalty_amount_tpl",
        name="忠诚数量门客",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    guest = Guest.objects.create(manor=manor, template=template, force=10, intellect=10, loyalty=50)

    with pytest.raises(AssertionError, match="invalid guest loyalty amount"):
        increase_guest_loyalty_by_ids([guest.id], amount=0)


@pytest.mark.django_db
def test_increase_guest_loyalty_by_ids_rejects_invalid_guest_id(django_user_model):
    user = django_user_model.objects.create_user(username="loyalty_bad_id", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="loyalty_bad_id_tpl",
        name="忠诚坏ID门客",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    Guest.objects.create(manor=manor, template=template, force=10, intellect=10, loyalty=50)

    with pytest.raises(AssertionError, match="invalid guest loyalty guest id"):
        increase_guest_loyalty_by_ids([1, "bad"])


@pytest.mark.django_db
def test_process_daily_loyalty_increases_paid_guests_and_decreases_unpaid_guests(django_user_model):
    user = django_user_model.objects.create_user(username="daily_loyalty", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="daily_loyalty_tpl",
        name="日结忠诚门客",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    paid_guest = Guest.objects.create(manor=manor, template=template, force=10, intellect=10, loyalty=50)
    unpaid_guest = Guest.objects.create(manor=manor, template=template, force=10, intellect=10, loyalty=50)
    Guest.objects.filter(pk__in=[paid_guest.pk, unpaid_guest.pk]).update(
        created_at=timezone.now() - timedelta(days=3),
    )

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    SalaryPayment.objects.create(manor=manor, guest=paid_guest, amount=100, for_date=yesterday)

    result = guest_tasks.process_daily_loyalty.run()

    paid_guest.refresh_from_db(fields=["loyalty", "loyalty_processed_for_date"])
    unpaid_guest.refresh_from_db(fields=["loyalty", "loyalty_processed_for_date"])
    assert paid_guest.loyalty == 51
    assert unpaid_guest.loyalty == 49
    assert paid_guest.loyalty_processed_for_date == today
    assert unpaid_guest.loyalty_processed_for_date == today
    assert "处理了 2 个门客的忠诚度" in result


@pytest.mark.django_db
def test_process_daily_loyalty_exempts_guests_created_during_the_unpaid_period(
    django_user_model,
    monkeypatch,
):
    user = django_user_model.objects.create_user(username="daily_loyalty_new_guest", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="daily_loyalty_new_guest_tpl",
        name="新招募免薪门客",
        rarity=GuestRarity.GRAY,
        base_attack=10,
        base_defense=10,
    )
    recruited_yesterday = Guest.objects.create(
        manor=manor,
        template=template,
        force=10,
        intellect=10,
        loyalty=20,
    )
    recruited_today = Guest.objects.create(
        manor=manor,
        template=template,
        force=10,
        intellect=10,
        loyalty=20,
    )
    Guest.objects.filter(pk=recruited_yesterday.pk).update(
        created_at=timezone.now() - timedelta(days=1),
    )
    monkeypatch.setattr(
        guest_tasks,
        "_should_defect",
        lambda *_args, **_kwargs: pytest.fail("newly recruited guests must not enter defection sampling"),
    )

    result = guest_tasks.process_daily_loyalty.run()

    today = timezone.localdate()
    recruited_yesterday.refresh_from_db(fields=["loyalty", "loyalty_processed_for_date"])
    recruited_today.refresh_from_db(fields=["loyalty", "loyalty_processed_for_date"])
    assert recruited_yesterday.loyalty == recruited_today.loyalty == 20
    assert recruited_yesterday.loyalty_processed_for_date == today
    assert recruited_today.loyalty_processed_for_date == today
    assert "处理了 2 个门客的忠诚度，0 个门客叛逃" == result
