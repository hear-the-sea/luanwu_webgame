from __future__ import annotations

from datetime import date

import pytest
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import MissionDailyLimitError
from gameplay.models import MissionTemplate
from tests.mission_views.support import assert_redirect, response_messages


@pytest.mark.django_db
class TestAcceptMissionView:
    def test_accept_mission_rejects_missing_mission_key(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.post(reverse("gameplay:accept_mission"), {})
        assert_redirect(response, reverse("gameplay:tasks"))
        assert any("请选择任务" in message for message in response_messages(response))

    def test_accept_mission_rejects_invalid_mission_key(self, manor_with_user):
        _manor, client = manor_with_user
        mission_key = "mission_not_exists_for_view_test"
        response = client.post(reverse("gameplay:accept_mission"), {"mission_key": mission_key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission_key}")
        assert any("任务不存在" in message for message in response_messages(response))

    def test_accept_mission_rejects_mission_unavailable_for_current_weekday(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_accept_weekday_unavailable_{manor.id}",
            name="非今日任务",
            available_weekdays=[2],
        )
        called = {"count": 0}
        monkeypatch.setattr(
            "gameplay.views.mission_helpers.timezone.localdate",
            lambda: date(2026, 5, 18),
        )

        def _unexpected_launch(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.missions.launch_mission", _unexpected_launch)

        response = client.post(reverse("gameplay:accept_mission"), {"mission_key": mission.key})

        assert_redirect(response, reverse("gameplay:tasks"))
        assert any("该任务今日未开放" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_accept_mission_rejects_when_action_lock_conflicts(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_accept_lock_conflict_{manor.id}",
            name="任务锁冲突",
            is_defense=True,
        )
        called = {"count": 0}

        monkeypatch.setattr(
            "gameplay.views.mission_helpers.acquire_mission_action_lock",
            lambda *_a, **_k: (False, "", None),
        )

        def _unexpected_launch(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.missions.launch_mission", _unexpected_launch)

        response = client.post(reverse("gameplay:accept_mission"), {"mission_key": mission.key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("任务请求处理中，请稍候重试" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_accept_mission_rejects_mixed_invalid_guest_ids(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_mission_invalid_guest_ids_{manor.id}",
            name="门客参数任务",
            is_defense=False,
            guest_only=True,
        )
        called = {"count": 0}

        def _unexpected_launch(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.missions.launch_mission", _unexpected_launch)

        response = client.post(
            reverse("gameplay:accept_mission"),
            {"mission_key": mission.key, "guest_ids": ["1", "abc"]},
        )

        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("门客选择有误" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_accept_mission_rejects_invalid_troop_quantity(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_mission_invalid_troop_qty_{manor.id}",
            name="护院参数任务",
            is_defense=False,
            guest_only=False,
        )
        called = {"count": 0}

        def _unexpected_launch(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.missions.launch_mission", _unexpected_launch)
        monkeypatch.setattr("battle.troops.troop_template_list", lambda: [{"key": "archer"}])

        response = client.post(
            reverse("gameplay:accept_mission"),
            {"mission_key": mission.key, "guest_ids": ["1"], "troop_archer": "bad"},
        )

        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("护院配置有误" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_accept_mission_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_accept_unexpected_{manor.id}",
            name="异常任务",
            is_defense=True,
        )

        monkeypatch.setattr(
            "gameplay.views.missions.launch_mission",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:accept_mission"), {"mission_key": mission.key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("操作失败，请稍后重试" in message for message in response_messages(response))

    def test_accept_mission_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_accept_runtime_{manor.id}",
            name="运行时任务",
            is_defense=True,
        )

        monkeypatch.setattr(
            "gameplay.views.missions.launch_mission",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:accept_mission"), {"mission_key": mission.key})

    def test_accept_mission_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_accept_legacy_value_{manor.id}",
            name="旧异常任务",
            is_defense=True,
        )

        monkeypatch.setattr(
            "gameplay.views.missions.launch_mission",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy mission error")),
        )

        with pytest.raises(ValueError, match="legacy mission error"):
            client.post(reverse("gameplay:accept_mission"), {"mission_key": mission.key})

    def test_accept_mission_known_error_shows_message(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_accept_known_{manor.id}",
            name="已知错误任务",
            is_defense=True,
        )

        monkeypatch.setattr(
            "gameplay.views.missions.launch_mission",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(MissionDailyLimitError()),
        )

        response = client.post(reverse("gameplay:accept_mission"), {"mission_key": mission.key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("今日该任务次数已耗尽" in message for message in response_messages(response))
