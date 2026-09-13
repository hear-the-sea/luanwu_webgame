"""
视图层 POST 操作测试
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from core.exceptions import BuildingUpgradingError
from gameplay.models import PlayerTechnology
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
class TestPostOperations:
    """POST操作测试"""

    def test_upgrade_building(self, manor_with_user):
        """建筑升级"""
        manor, client = manor_with_user
        manor.grain = manor.silver = 100000
        manor.save()
        building = manor.buildings.first()
        response = client.post(reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}))
        assert response.status_code == 302

    def test_upgrade_building_success_message_uses_server_timezone(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()
        server_eta = datetime(2026, 8, 8, 1, 2, 3, tzinfo=dt_timezone.utc)

        def fake_start_upgrade(target):
            target.upgrade_complete_at = server_eta

        monkeypatch.setattr("gameplay.views.buildings.start_upgrade", fake_start_upgrade)

        response = client.post(reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}))
        messages = [str(message) for message in get_messages(response.wsgi_request)]

        assert response.status_code == 302
        assert any("完成时间 09:02:03" in message for message in messages)

    def test_upgrade_building_known_error_shows_message(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()

        monkeypatch.setattr(
            "gameplay.views.buildings.start_upgrade",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(BuildingUpgradingError("upgrade blocked")),
        )

        response = client.post(reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}))
        assert response.status_code == 302
        assert response.url == reverse("gameplay:dashboard")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("upgrade blocked" in m for m in messages)

    def test_upgrade_building_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()

        monkeypatch.setattr(
            "gameplay.views.buildings.start_upgrade",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy upgrade blocked")),
        )

        with pytest.raises(ValueError, match="legacy upgrade blocked"):
            client.post(reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}))

    def test_upgrade_building_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()

        monkeypatch.setattr(
            "gameplay.views.buildings.start_upgrade",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}))
        assert response.status_code == 302
        assert response.url == reverse("gameplay:dashboard")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_upgrade_building_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()

        monkeypatch.setattr(
            "gameplay.views.buildings.start_upgrade",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}))

    def test_upgrade_building_redirects_to_safe_next_url(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()

        monkeypatch.setattr("gameplay.views.buildings.start_upgrade", lambda *_args, **_kwargs: None)
        next_url = f"{reverse('gameplay:dashboard')}#building-{building.pk}"

        response = client.post(
            reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}),
            {"next": next_url},
        )
        assert response.status_code == 302
        assert response.url == next_url

    def test_upgrade_building_rejects_unsafe_next_url(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        building = manor.buildings.first()

        monkeypatch.setattr("gameplay.views.buildings.start_upgrade", lambda *_args, **_kwargs: None)

        response = client.post(
            reverse("gameplay:upgrade_building", kwargs={"pk": building.pk}),
            {"next": "https://evil.example/phish"},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:dashboard")

    def test_cancel_building_upgrade_clears_timer_without_refunding_costs(self, manor_with_user):
        manor, client = manor_with_user
        building = manor.buildings.first()
        building.is_upgrading = True
        building.upgrade_complete_at = timezone.now() + timedelta(minutes=5)
        building.save(update_fields=["is_upgrading", "upgrade_complete_at"])
        manor.silver = 1234
        manor.grain = 5678
        manor.action_points = 4
        manor.save(update_fields=["silver", "grain", "action_points"])
        resource_event_count = manor.resource_events.count()

        response = client.post(reverse("gameplay:cancel_building_upgrade", args=[building.pk]))

        building.refresh_from_db()
        manor.refresh_from_db()
        assert response.status_code == 302
        assert response.url == reverse("gameplay:dashboard")
        assert building.level == 1
        assert building.is_upgrading is False
        assert building.upgrade_complete_at is None
        assert (manor.silver, manor.grain, manor.action_points) == (1234, 5678, 4)
        assert manor.resource_events.count() == resource_event_count
        assert any("不会返还" in str(message) for message in get_messages(response.wsgi_request))

    def test_cancel_technology_upgrade_clears_timer_without_refunding_silver(self, manor_with_user):
        manor, client = manor_with_user
        manor.silver = 2468
        manor.save(update_fields=["silver"])
        technology = PlayerTechnology.objects.create(
            manor=manor,
            tech_key="dao_attack",
            level=1,
            is_upgrading=True,
            upgrade_complete_at=timezone.now() + timedelta(minutes=5),
        )
        resource_event_count = manor.resource_events.count()

        response = client.post(reverse("gameplay:cancel_technology_upgrade", args=[technology.tech_key]))

        technology.refresh_from_db()
        manor.refresh_from_db()
        assert response.status_code == 302
        assert response.url == reverse("gameplay:dashboard")
        assert technology.level == 1
        assert technology.is_upgrading is False
        assert technology.upgrade_complete_at is None
        assert manor.silver == 2468
        assert manor.resource_events.count() == resource_event_count
        assert any("不会返还" in str(message) for message in get_messages(response.wsgi_request))

    def test_cancel_building_upgrade_cannot_change_another_manor(self, manor_with_user, user_factory):
        _manor, client = manor_with_user
        other_manor = ensure_manor(user_factory())
        building = other_manor.buildings.first()
        building.is_upgrading = True
        building.upgrade_complete_at = timezone.now() + timedelta(minutes=5)
        building.save(update_fields=["is_upgrading", "upgrade_complete_at"])

        response = client.post(reverse("gameplay:cancel_building_upgrade", args=[building.pk]))

        building.refresh_from_db()
        assert response.status_code == 302
        assert building.is_upgrading is True

    def test_cancel_technology_upgrade_cannot_change_another_manor(self, manor_with_user, user_factory):
        _manor, client = manor_with_user
        other_manor = ensure_manor(user_factory())
        technology = PlayerTechnology.objects.create(
            manor=other_manor,
            tech_key="dao_attack",
            level=1,
            is_upgrading=True,
            upgrade_complete_at=timezone.now() + timedelta(minutes=5),
        )

        response = client.post(reverse("gameplay:cancel_technology_upgrade", args=[technology.tech_key]))

        technology.refresh_from_db()
        assert response.status_code == 302
        assert technology.is_upgrading is True

    def test_cancel_upgrade_does_not_cancel_an_already_due_upgrade(self, manor_with_user):
        manor, client = manor_with_user
        due_at = timezone.now() - timedelta(seconds=1)
        building = manor.buildings.first()
        building.is_upgrading = True
        building.upgrade_complete_at = due_at
        building.save(update_fields=["is_upgrading", "upgrade_complete_at"])
        technology = PlayerTechnology.objects.create(
            manor=manor,
            tech_key="dao_attack",
            level=1,
            is_upgrading=True,
            upgrade_complete_at=due_at,
        )

        client.post(reverse("gameplay:cancel_building_upgrade", args=[building.pk]))
        client.post(reverse("gameplay:cancel_technology_upgrade", args=[technology.tech_key]))

        building.refresh_from_db()
        technology.refresh_from_db()
        assert building.is_upgrading is True
        assert technology.is_upgrading is True

    @pytest.mark.parametrize(
        ("route_name", "route_args"),
        [
            ("gameplay:cancel_building_upgrade", [1]),
            ("gameplay:cancel_technology_upgrade", ["dao_attack"]),
        ],
    )
    def test_cancel_upgrade_requires_post(self, manor_with_user, route_name, route_args):
        _manor, client = manor_with_user

        response = client.get(reverse(route_name, args=route_args))

        assert response.status_code == 405

    def test_delete_messages_empty(self, manor_with_user):
        """删除消息 - 空选择"""
        _manor, client = manor_with_user
        response = client.post(reverse("gameplay:delete_messages"))
        assert response.status_code == 302
