"""
募兵系统视图测试
"""

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted

from core.exceptions import TroopRecruitmentError
from gameplay.models import TroopRecruitment
from gameplay.services.utils import cache as cache_utils
from guests.views.recruit import _invalidate_recruitment_hall_cache_for_manor


@pytest.mark.django_db
class TestRecruitmentViews:
    """募兵系统视图测试"""

    def test_troop_recruitment_page(self, manor_with_user):
        """募兵页面"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:troop_recruitment"))
        assert response.status_code == 200
        assert "recruitment_options" in response.context

    def test_troop_recruitment_page_category_filter(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:troop_recruitment") + "?category=jian")
        assert response.status_code == 200
        assert response.context["current_category"] == "jian"
        assert "recruitment_categories" in response.context
        options = response.context["recruitment_options"]
        assert options
        assert all(option.get("troop_class") == "jian" for option in options)

    def test_troop_recruitment_page_active_queue_has_refresh_endpoint(self, manor_with_user):
        manor, client = manor_with_user
        TroopRecruitment.objects.create(
            manor=manor,
            troop_key="dao_guard",
            troop_name="刀盾护院",
            quantity=1,
            equipment_costs={},
            retainer_cost=1,
            base_duration=60,
            actual_duration=60,
            complete_at=timezone.now() + timezone.timedelta(minutes=1),
        )

        response = client.get(reverse("gameplay:troop_recruitment"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_troop_recruitments_api") in body
        assert 'data-refresh-method="post"' in body

    def test_troop_recruitment_page_uses_explicit_read_helper(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"prepared": 0}

        monkeypatch.setattr(
            "gameplay.views.recruitment.get_prepared_manor_for_read",
            lambda request, **kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.recruitment.get_troop_recruitment_context",
            lambda current_manor, *, selected_category: (
                {
                    "current_category": selected_category,
                    "recruitment_options": [],
                    "recruitment_categories": [{"key": "all", "name": "全部"}],
                    "active_recruitments": [],
                    "player_troops": [],
                    "training_level": 0,
                    "citang_level": 0,
                    "can_recruit": False,
                    "speed_bonus_percent": 0,
                    "training_multiplier": 1,
                    "citang_multiplier": 1,
                    "is_recruiting": False,
                }
                if current_manor is manor
                else {}
            ),
        )

        response = client.get(reverse("gameplay:troop_recruitment"))

        assert response.status_code == 200
        assert calls["prepared"] == 1

    def test_troop_recruitment_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr("gameplay.views.recruitment.get_prepared_manor_for_read", lambda request, **kwargs: manor)

        def _fake_selector(current_manor, *, selected_category):
            calls["selector"] += 1
            assert current_manor is manor
            assert selected_category == "dao"
            return {
                "current_category": selected_category,
                "recruitment_options": [{"key": "dao_guard", "troop_class": "dao"}],
                "recruitment_categories": [{"key": "all", "name": "全部"}, {"key": "dao", "name": "刀系"}],
                "active_recruitments": [],
                "player_troops": [],
                "training_level": 1,
                "citang_level": 1,
                "can_recruit": True,
                "speed_bonus_percent": 100,
                "training_multiplier": 1.5,
                "citang_multiplier": 2.0,
                "is_recruiting": False,
            }

        monkeypatch.setattr("gameplay.views.recruitment.get_troop_recruitment_context", _fake_selector)

        response = client.get(reverse("gameplay:troop_recruitment") + "?category=dao")

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert response.context["current_category"] == "dao"
        assert response.context["recruitment_options"] == [{"key": "dao_guard", "troop_class": "dao"}]

    def test_start_troop_recruitment_database_error_does_not_500(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.recruitment.recruitment.start_troop_recruitment",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:start_troop_recruitment"),
            {"troop_key": "any", "quantity": "1"},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:troop_recruitment")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_start_troop_recruitment_unexpected_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.recruitment.recruitment.start_troop_recruitment",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:start_troop_recruitment"),
                {"troop_key": "any", "quantity": "1"},
            )

    def test_start_troop_recruitment_rejects_invalid_quantity(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        called = {"count": 0}

        def _unexpected_start(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.services.recruitment.recruitment.start_troop_recruitment", _unexpected_start)

        response = client.post(
            reverse("gameplay:start_troop_recruitment"),
            {"troop_key": "any", "quantity": "bad"},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:troop_recruitment")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("无效的数量" in m for m in messages)
        assert called["count"] == 0

    def test_start_troop_recruitment_known_error_shows_message(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.recruitment.recruitment.start_troop_recruitment",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TroopRecruitmentError("recruit blocked")),
        )

        response = client.post(
            reverse("gameplay:start_troop_recruitment"),
            {"troop_key": "any", "quantity": "1"},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:troop_recruitment")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("recruit blocked" in m for m in messages)

    def test_start_troop_recruitment_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.recruitment.recruitment.start_troop_recruitment",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload")),
        )

        with pytest.raises(ValueError, match="bad payload"):
            client.post(
                reverse("gameplay:start_troop_recruitment"),
                {"troop_key": "any", "quantity": "1"},
            )


def test_invalidate_recruitment_hall_cache_for_manor_tolerates_connection_interrupted(monkeypatch):
    monkeypatch.setattr(
        cache_utils,
        "invalidate_recruitment_hall_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    assert _invalidate_recruitment_hall_cache_for_manor(1) is False
