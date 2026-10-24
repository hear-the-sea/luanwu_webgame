from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import MissionCannotRetreatError, ScoutRetreatStateError
from gameplay.models import MissionRun, MissionTemplate
from tests.mission_views.support import assert_redirect, build_scout_record, response_messages


@pytest.mark.django_db
class TestRetreatViews:
    def test_retreat_mission_returns_home_and_starts_return_journey(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(key=f"retreat_home_{manor.id}", name="返程测试")
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE, travel_time=300)
        monkeypatch.setattr("gameplay.services.missions_impl.execution.schedule_mission_completion", lambda _run: None)

        response = client.post(reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}))

        assert_redirect(response, reverse("home"))
        run.refresh_from_db()
        assert run.is_retreating is True
        assert run.return_at is not None
        assert any("已撤退" in message for message in response_messages(response))

    def test_retreat_mission_known_error_returns_home(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(key=f"retreat_home_error_{manor.id}", name="撤退受限")
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE)

        def reject_retreat(_run):
            raise MissionCannotRetreatError(reason="returning")

        monkeypatch.setattr("gameplay.views.missions.request_retreat", reject_retreat)
        response = client.post(reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}))

        assert_redirect(response, reverse("home"))
        assert any("已进入返程" in message for message in response_messages(response))

    def test_retreat_mission_rejects_when_action_lock_conflicts(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_retreat_lock_conflict_{manor.id}",
            name="撤退锁冲突任务",
            is_defense=False,
        )
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE)
        called = {"count": 0}

        monkeypatch.setattr(
            "gameplay.views.mission_helpers.acquire_mission_action_lock",
            lambda *_a, **_k: (False, "", None),
        )

        def _unexpected_retreat(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.missions.request_retreat", _unexpected_retreat)

        response = client.post(reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}))
        assert_redirect(response, reverse("home"))
        assert any("任务请求处理中，请稍候重试" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_retreat_scout_rejects_when_action_lock_conflicts(self, manor_with_user, monkeypatch, django_user_model):
        attacker, client = manor_with_user
        record = build_scout_record(
            attacker=attacker,
            django_user_model=django_user_model,
            username=f"scout_def_lock_{attacker.id}",
        )
        called = {"count": 0}

        monkeypatch.setattr(
            "gameplay.views.mission_helpers.acquire_mission_action_lock",
            lambda *_a, **_k: (False, "", None),
        )

        def _unexpected_retreat(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.services.raid.request_scout_retreat", _unexpected_retreat)

        response = client.post(reverse("gameplay:scout_retreat", kwargs={"pk": record.pk}))
        assert_redirect(response, reverse("home"))
        assert any("任务请求处理中，请稍候重试" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_retreat_mission_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_retreat_unexpected_{manor.id}",
            name="撤退异常任务",
            is_defense=False,
        )
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE)

        monkeypatch.setattr(
            "gameplay.views.missions.request_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}))
        assert_redirect(response, reverse("home"))
        assert any("操作失败，请稍后重试" in message for message in response_messages(response))

    def test_retreat_mission_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_retreat_runtime_{manor.id}",
            name="撤退运行时任务",
            is_defense=False,
        )
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE)

        monkeypatch.setattr(
            "gameplay.views.missions.request_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}))

    def test_retreat_mission_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_retreat_legacy_value_{manor.id}",
            name="撤退旧异常任务",
            is_defense=False,
        )
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE)

        monkeypatch.setattr(
            "gameplay.views.missions.request_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy retreat error")),
        )

        with pytest.raises(ValueError, match="legacy retreat error"):
            client.post(reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}))

    def test_retreat_scout_database_error_does_not_500(self, manor_with_user, monkeypatch, django_user_model):
        attacker, client = manor_with_user
        record = build_scout_record(
            attacker=attacker,
            django_user_model=django_user_model,
            username=f"scout_def_{attacker.id}",
        )

        monkeypatch.setattr(
            "gameplay.services.raid.request_scout_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:scout_retreat", kwargs={"pk": record.pk}))
        assert_redirect(response, reverse("home"))
        assert any("操作失败，请稍后重试" in message for message in response_messages(response))

    def test_retreat_scout_programming_error_bubbles_up(self, manor_with_user, monkeypatch, django_user_model):
        attacker, client = manor_with_user
        record = build_scout_record(
            attacker=attacker,
            django_user_model=django_user_model,
            username=f"scout_def_runtime_{attacker.id}",
        )

        monkeypatch.setattr(
            "gameplay.services.raid.request_scout_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:scout_retreat", kwargs={"pk": record.pk}))

    def test_retreat_scout_known_error_shows_message(self, manor_with_user, monkeypatch, django_user_model):
        attacker, client = manor_with_user
        record = build_scout_record(
            attacker=attacker,
            django_user_model=django_user_model,
            username=f"scout_def_known_{attacker.id}",
        )

        monkeypatch.setattr(
            "gameplay.services.raid.request_scout_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ScoutRetreatStateError()),
        )

        response = client.post(reverse("gameplay:scout_retreat", kwargs={"pk": record.pk}))
        assert_redirect(response, reverse("home"))
        assert any("当前状态无法撤退" in message for message in response_messages(response))

    def test_retreat_scout_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch, django_user_model):
        attacker, client = manor_with_user
        record = build_scout_record(
            attacker=attacker,
            django_user_model=django_user_model,
            username=f"scout_def_legacy_{attacker.id}",
        )

        monkeypatch.setattr(
            "gameplay.services.raid.request_scout_retreat",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy scout retreat")),
        )

        with pytest.raises(ValueError, match="legacy scout retreat"):
            client.post(reverse("gameplay:scout_retreat", kwargs={"pk": record.pk}))
