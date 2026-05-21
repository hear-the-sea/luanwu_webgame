from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import ActionPointsInsufficientError
from gameplay.services.action_points import (
    ACTION_POINT_EXPEDITION_COST,
    ACTION_POINTS_MAX,
    consume_action_points_for_expedition,
    get_current_action_points,
)
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db


def test_get_current_action_points_caps_recovery_at_max(django_user_model):
    user = django_user_model.objects.create_user(username="ap_recovery_user", password="pass12345")
    manor = ensure_manor(user)
    now = timezone.now()
    manor.action_points = ACTION_POINTS_MAX - 3
    manor.action_points_updated_at = now - timedelta(minutes=10)
    manor.save(update_fields=["action_points", "action_points_updated_at"])

    assert get_current_action_points(manor, now=now) == ACTION_POINTS_MAX


def test_consume_action_points_for_expedition_deducts_after_recovery(django_user_model):
    user = django_user_model.objects.create_user(username="ap_consume_user", password="pass12345")
    manor = ensure_manor(user)
    now = timezone.now()
    manor.action_points = 8
    manor.action_points_updated_at = now - timedelta(minutes=4)
    manor.save(update_fields=["action_points", "action_points_updated_at"])

    remaining = consume_action_points_for_expedition(manor, now=now)

    assert remaining == 0
    manor.refresh_from_db()
    assert manor.action_points == 0
    assert manor.action_points_updated_at == now


def test_consume_action_points_preserves_partial_recovery_progress(django_user_model):
    user = django_user_model.objects.create_user(username="ap_partial_progress_user", password="pass12345")
    manor = ensure_manor(user)
    now = timezone.now()
    original_updated_at = now - timedelta(seconds=359)
    manor.action_points = 8
    manor.action_points_updated_at = original_updated_at
    manor.save(update_fields=["action_points", "action_points_updated_at"])

    remaining = consume_action_points_for_expedition(manor, now=now)

    assert remaining == 0
    manor.refresh_from_db()
    assert manor.action_points == 0
    assert manor.action_points_updated_at == original_updated_at + timedelta(seconds=240)
    assert get_current_action_points(manor, now=now + timedelta(seconds=1)) == 1


def test_consume_action_points_for_expedition_rejects_when_insufficient(django_user_model):
    user = django_user_model.objects.create_user(username="ap_insufficient_user", password="pass12345")
    manor = ensure_manor(user)
    now = timezone.now()
    manor.action_points = ACTION_POINT_EXPEDITION_COST - 1
    manor.action_points_updated_at = now
    manor.save(update_fields=["action_points", "action_points_updated_at"])

    with pytest.raises(ActionPointsInsufficientError, match="行动力不足"):
        consume_action_points_for_expedition(manor, now=now)

    manor.refresh_from_db()
    assert manor.action_points == ACTION_POINT_EXPEDITION_COST - 1
