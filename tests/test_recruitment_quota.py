from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.selectors.recruitment import get_recruitment_hall_context
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestRecruitment, RecruitmentExtraAttempt, RecruitmentPool
from guests.services.recruitment_quota import (
    RECRUITMENT_CARD_KEY,
    add_recruitment_extra_attempt,
    add_recruitment_extra_attempt_with_item_cost,
)


def _create_recruitment_card(manor, *, quantity: int) -> InventoryItem:
    template, _created = ItemTemplate.objects.get_or_create(
        key=RECRUITMENT_CARD_KEY,
        defaults={
            "name": "招募卡",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": False,
        },
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


@pytest.mark.django_db
def test_recruitment_card_adds_unlimited_daily_quota_and_consumes_one_card(django_user_model, load_guest_data):
    user = django_user_model.objects.create_user(username="recruitment_quota_item", password="pass123")
    manor = ensure_manor(user)
    pool = RecruitmentPool.objects.get(key="cunmu")
    inventory_item = _create_recruitment_card(manor, quantity=2)

    assert add_recruitment_extra_attempt_with_item_cost(manor, pool) == 1
    assert add_recruitment_extra_attempt_with_item_cost(manor, pool) == 2

    extra = RecruitmentExtraAttempt.objects.get(manor=manor, pool=pool, date=timezone.localdate())
    assert not InventoryItem.objects.filter(pk=inventory_item.pk).exists()
    assert extra.extra_count == 2


@pytest.mark.django_db
def test_recruitment_hall_context_combines_daily_limit_and_card_bonus(game_data, django_user_model, load_guest_data):
    user = django_user_model.objects.create_user(username="recruitment_quota_context", password="pass123")
    manor = ensure_manor(user)
    pool = RecruitmentPool.objects.get(key="cunmu")
    now = timezone.now()
    GuestRecruitment.objects.create(
        manor=manor,
        pool=pool,
        cost={},
        draw_count=1,
        duration_seconds=1,
        seed=1,
        status=GuestRecruitment.Status.COMPLETED,
        complete_at=now,
        finished_at=now,
    )
    add_recruitment_extra_attempt(manor, pool, count=3)

    context = get_recruitment_hall_context(manor, records_limit=5, use_cache=False)
    pool_context = next(item for item in context["pools"] if item.pk == pool.pk)

    assert pool_context.daily_recruited_count == 1
    assert pool_context.daily_recruitment_limit == 6
    assert pool_context.daily_recruitment_remaining == 5
    assert pool_context.recruitment_card_uses == 3


@pytest.mark.django_db
def test_recruitment_card_view_consumes_card_and_returns_hall_fragments(game_data, django_user_model, load_guest_data):
    user = django_user_model.objects.create_user(username="recruitment_card_view", password="pass123")
    manor = ensure_manor(user)
    pool = RecruitmentPool.objects.get(key="cunmu")
    _create_recruitment_card(manor, quantity=1)
    client = Client()
    assert client.login(username="recruitment_card_view", password="pass123")

    response = client.post(
        reverse("guests:use_recruitment_card"),
        {"pool_id": str(pool.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "今日额度 +1" in payload["message"]
    assert "recruit-pools-section" in payload["hall_pools_html"]
    assert add_recruitment_extra_attempt(manor, pool) == 2
