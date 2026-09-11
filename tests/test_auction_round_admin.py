import pytest
from django.urls import reverse
from django.utils import timezone

from trade.admin import AuctionRoundAdminForm
from trade.models import AuctionRound


@pytest.mark.django_db
@pytest.mark.parametrize("status", [AuctionRound.Status.COMPLETED, AuctionRound.Status.ACTIVE])
def test_auction_round_admin_add_accepts_required_start_time(client, django_user_model, status):
    user = django_user_model.objects.create_superuser(username="round_admin", password="pass123")
    client.force_login(user)
    add_url = reverse("admin:trade_auctionround_add")
    page = client.get(add_url)
    assert page.status_code == 200
    assert "start_at" in page.context["adminform"].form.fields

    response = client.post(
        add_url,
        {
            "round_number": 99,
            "status": status,
            "start_at_0": "2026-09-10",
            "start_at_1": "10:00:00",
            "end_at_0": "2026-09-11",
            "end_at_1": "10:00:00",
            "slots-TOTAL_FORMS": 0,
            "slots-INITIAL_FORMS": 0,
            "slots-MIN_NUM_FORMS": 0,
            "slots-MAX_NUM_FORMS": 1000,
            "_save": "保存",
        },
    )

    assert response.status_code == 302
    auction_round = AuctionRound.objects.get(round_number=99)
    assert auction_round.start_at is not None
    change_page = client.get(reverse("admin:trade_auctionround_change", args=[auction_round.pk]))
    assert change_page.status_code == 200
    assert "start_at" not in change_page.context["adminform"].form.fields


@pytest.mark.django_db
@pytest.mark.parametrize("status", [AuctionRound.Status.ACTIVE, AuctionRound.Status.SETTLING])
def test_auction_round_admin_reports_duplicate_live_status(client, django_user_model, status):
    user = django_user_model.objects.create_superuser(username="round_conflict_admin", password="pass123")
    client.force_login(user)
    existing = AuctionRound.objects.create(
        round_number=1, status=status, start_at=timezone.now(), end_at=timezone.now()
    )
    response = client.post(
        reverse("admin:trade_auctionround_add"),
        {
            "round_number": 2,
            "status": status,
            "start_at_0": "2026-09-10",
            "start_at_1": "10:00:00",
            "end_at_0": "2026-09-11",
            "end_at_1": "10:00:00",
            "slots-TOTAL_FORMS": 0,
            "slots-INITIAL_FORMS": 0,
            "slots-MIN_NUM_FORMS": 0,
            "slots-MAX_NUM_FORMS": 1000,
            "_save": "保存",
        },
    )

    assert response.status_code == 200
    assert "已存在相同状态的拍卖轮次" in str(response.context["adminform"].form.errors)
    assert list(AuctionRound.objects.values_list("pk", flat=True)) == [existing.pk]

    form = AuctionRoundAdminForm(
        instance=existing,
        data={
            "round_number": existing.round_number,
            "status": status,
            "start_at": existing.start_at,
            "end_at": existing.end_at,
        },
    )
    assert form.is_valid(), form.errors
