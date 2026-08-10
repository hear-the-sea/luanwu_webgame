"""
视图层 POST 操作测试
"""

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import BuildingUpgradingError


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

    def test_delete_messages_empty(self, manor_with_user):
        """删除消息 - 空选择"""
        _manor, client = manor_with_user
        response = client.post(reverse("gameplay:delete_messages"))
        assert response.status_code == 302
