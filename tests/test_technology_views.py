from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from core.exceptions import TechnologyError
from gameplay.models import PlayerTechnology


@pytest.mark.django_db
class TestTechnologyViews:
    """科技系统视图测试"""

    def test_technology_page(self, manor_with_user):
        """科技页面"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:technology"))
        assert response.status_code == 200
        assert "technologies" in response.context
        content = response.content.decode("utf-8")
        assert 'class="tw-building-headline"' in content
        assert "等级 0 /" in content

    def test_technology_martial_tab(self, manor_with_user):
        """武艺科技标签页"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:technology") + "?tab=martial")
        assert response.status_code == 200
        assert response.context["current_tab"] == "martial"

    def test_technology_invalid_tab_falls_back_to_basic(self, manor_with_user):
        """非法科技标签页应回退到基础分类。"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:technology") + "?tab=unknown")
        assert response.status_code == 200
        assert response.context["current_tab"] == "basic"

    def test_technology_page_active_upgrade_has_refresh_endpoint(self, manor_with_user):
        manor, client = manor_with_user
        PlayerTechnology.objects.create(
            manor=manor,
            tech_key="dao_attack",
            level=0,
            is_upgrading=True,
            upgrade_complete_at=timezone.now() + timezone.timedelta(minutes=1),
        )

        response = client.get(reverse("gameplay:technology") + "?tab=martial&troop=dao")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_technology_upgrades_api") in body
        assert 'data-refresh-method="post"' in body

    def test_technology_page_uses_explicit_read_helper(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"prepared": 0}

        monkeypatch.setattr(
            "gameplay.views.technology.get_prepared_manor_for_read",
            lambda request, **kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.technology.get_technology_page_context",
            lambda current_manor, *, current_tab, current_troop_class: (
                {
                    "categories": [],
                    "current_tab": current_tab or "basic",
                    "martial_groups": [],
                    "troop_classes": [],
                    "current_troop_class": current_troop_class,
                    "technologies": [],
                }
                if current_manor is manor
                else {}
            ),
        )

        response = client.get(reverse("gameplay:technology"))

        assert response.status_code == 200
        assert calls["prepared"] == 1

    def test_technology_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr("gameplay.views.technology.get_prepared_manor_for_read", lambda request, **kwargs: manor)

        def _fake_selector(current_manor, *, current_tab, current_troop_class):
            calls["selector"] += 1
            assert current_manor is manor
            assert current_tab == "martial"
            assert current_troop_class == "qiang"
            return {
                "categories": [{"key": "martial", "name": "武艺"}],
                "current_tab": "martial",
                "martial_groups": [{"class_key": "qiang", "techs": []}],
                "troop_classes": [{"key": "qiang", "name": "枪类"}],
                "current_troop_class": "qiang",
                "technologies": [],
            }

        monkeypatch.setattr("gameplay.views.technology.get_technology_page_context", _fake_selector)

        response = client.get(reverse("gameplay:technology") + "?tab=martial&troop=qiang")

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert response.context["current_tab"] == "martial"
        assert response.context["current_troop_class"] == "qiang"
        assert response.context["martial_groups"] == [{"class_key": "qiang", "techs": []}]

    def test_upgrade_technology_known_error_shows_message(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.technology.upgrade_technology",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TechnologyError("tech blocked")),
        )

        response = client.post(
            reverse("gameplay:upgrade_technology", kwargs={"tech_key": "dao_attack"}),
            {"tab": "basic"},
        )
        assert response.status_code == 302
        assert response.url == f"{reverse('gameplay:technology')}?tab=basic"
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("tech blocked" in m for m in messages)

    def test_upgrade_technology_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.technology.upgrade_technology",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy tech blocked")),
        )

        with pytest.raises(ValueError, match="legacy tech blocked"):
            client.post(
                reverse("gameplay:upgrade_technology", kwargs={"tech_key": "dao_attack"}),
                {"tab": "basic"},
            )

    def test_upgrade_technology_database_error_does_not_500(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.technology.upgrade_technology",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:upgrade_technology", kwargs={"tech_key": "dao_attack"}),
            {"tab": "basic"},
        )
        assert response.status_code == 302
        assert response.url == f"{reverse('gameplay:technology')}?tab=basic"
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_upgrade_technology_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.technology.upgrade_technology",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:upgrade_technology", kwargs={"tech_key": "dao_attack"}),
                {"tab": "basic"},
            )

    def test_upgrade_technology_redirects_to_safe_next_url(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.technology.upgrade_technology",
            lambda *_args, **_kwargs: {"message": "ok"},
        )
        next_url = f"{reverse('gameplay:technology')}?tab=martial&troop=dao#tech-dao_attack"

        response = client.post(
            reverse("gameplay:upgrade_technology", kwargs={"tech_key": "dao_attack"}),
            {"tab": "basic", "next": next_url},
        )
        assert response.status_code == 302
        assert response.url == next_url

    def test_upgrade_technology_rejects_unsafe_next_url(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.technology.upgrade_technology",
            lambda *_args, **_kwargs: {"message": "ok"},
        )

        response = client.post(
            reverse("gameplay:upgrade_technology", kwargs={"tech_key": "dao_attack"}),
            {"tab": "martial", "troop": "dao", "next": "https://evil.example/phish"},
        )
        assert response.status_code == 302
        assert response.url == f"{reverse('gameplay:technology')}?tab=martial&troop=dao"
