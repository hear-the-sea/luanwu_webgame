from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model

import gameplay.services.missions_impl.attempts as mission_attempts_service
from core.exceptions import MissionDailyLimitError
from gameplay.models import MissionExtraAttempt, MissionTemplate
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl.attempts import (
    MISSION_CARD_DAILY_LIMIT_PER_MISSION,
    add_mission_extra_attempt,
    get_mission_daily_limit,
)
from gameplay.services.missions_impl.time_utils import get_today_date_range


@pytest.mark.django_db
def test_add_mission_extra_attempt_rejects_non_positive_count():
    user = get_user_model().objects.create_user(username="mission_extra_attempt_invalid", password="pass123")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(key="mission_attempt_invalid", name="任务次数校验")

    with pytest.raises(AssertionError, match="invalid mission extra attempt count"):
        add_mission_extra_attempt(manor, mission, 0)


@pytest.mark.django_db
def test_add_mission_extra_attempt_rejects_bool_count():
    user = get_user_model().objects.create_user(username="mission_extra_attempt_bool", password="pass123")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(key="mission_attempt_bool", name="任务次数布尔校验")

    with pytest.raises(AssertionError, match="invalid mission extra attempt count"):
        add_mission_extra_attempt(manor, mission, True)


@pytest.mark.django_db
def test_add_mission_extra_attempt_caps_each_mission_at_five_per_day():
    user = get_user_model().objects.create_user(username="mission_extra_attempt_limit", password="pass123")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(key="mission_attempt_limit", name="任务卡每日上限")

    assert add_mission_extra_attempt(manor, mission, MISSION_CARD_DAILY_LIMIT_PER_MISSION) == 5

    with pytest.raises(MissionDailyLimitError, match="该任务今日最多使用 5 张任务卡"):
        add_mission_extra_attempt(manor, mission, 1)

    assert mission_attempts_service.get_mission_extra_attempts(manor, mission) == 5


@pytest.mark.django_db
def test_add_mission_extra_attempt_uses_per_mission_limit():
    user = get_user_model().objects.create_user(username="mission_extra_attempt_custom_limit", password="pass123")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="mission_attempt_custom_limit",
        name="任务卡独立上限",
        mission_card_daily_limit=1,
    )

    assert add_mission_extra_attempt(manor, mission, 1) == 1

    with pytest.raises(MissionDailyLimitError, match="该任务今日最多使用 1 张任务卡"):
        add_mission_extra_attempt(manor, mission, 1)

    assert mission_attempts_service.get_mission_extra_attempts(manor, mission) == 1


@pytest.mark.django_db
def test_zero_mission_card_limit_does_not_disable_base_mission_attempts():
    user = get_user_model().objects.create_user(username="mission_extra_attempt_disabled", password="pass123")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="mission_attempt_disabled",
        name="禁用任务卡任务",
        daily_limit=1,
        mission_card_daily_limit=0,
    )

    with pytest.raises(MissionDailyLimitError, match="该任务不可使用任务卡"):
        add_mission_extra_attempt(manor, mission, 1)

    assert MissionExtraAttempt.objects.filter(manor=manor, mission=mission).exists() is False
    assert get_mission_daily_limit(manor, mission) == 1


@pytest.mark.django_db
def test_add_mission_extra_attempt_limit_is_scoped_to_natural_day():
    user = get_user_model().objects.create_user(username="mission_extra_attempt_new_day", password="pass123")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(key="mission_attempt_new_day", name="任务卡跨日重置")
    _, _, today = get_today_date_range()
    MissionExtraAttempt.objects.create(
        manor=manor,
        mission=mission,
        date=today - timedelta(days=1),
        extra_count=MISSION_CARD_DAILY_LIMIT_PER_MISSION,
    )

    assert add_mission_extra_attempt(manor, mission, 1) == 1


def test_get_mission_daily_limit_rejects_non_positive_daily_limit(monkeypatch):
    mission = type("_Mission", (), {"daily_limit": 0})()
    monkeypatch.setattr(mission_attempts_service, "get_mission_extra_attempts", lambda *_a, **_k: 0)

    with pytest.raises(AssertionError, match="invalid mission daily_limit"):
        get_mission_daily_limit(object(), mission)


def test_get_mission_daily_limit_rejects_invalid_extra_attempts(monkeypatch):
    mission = type("_Mission", (), {"daily_limit": 3})()
    monkeypatch.setattr(mission_attempts_service, "get_mission_extra_attempts", lambda *_a, **_k: True)

    with pytest.raises(AssertionError, match="invalid mission extra attempts"):
        get_mission_daily_limit(object(), mission)
