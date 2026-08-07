"""
地图状态与详情 API 测试
"""

import json

import pytest
from django.urls import reverse

from gameplay.models import BotBackfillDemand


@pytest.mark.django_db
class TestMapStatusAPI:
    def test_map_search_by_region(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:map_search_api"), {"type": "region", "region": manor.region})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "results" in data

    def test_map_search_by_region_includes_self(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:map_search_api"), {"type": "region", "region": manor.region})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        ids = {row.get("id") for row in data.get("results", [])}
        assert manor.id in ids

    def test_map_search_by_region_is_read_only(self, manor_with_user, settings):
        manor, client = manor_with_user
        manor.newbie_protection_until = None
        manor.peace_shield_until = None
        manor.save(update_fields=["newbie_protection_until", "peace_shield_until"])
        settings.VIRTUAL_PLAYER_CONFIG = {
            "population": {"min_attackable_per_band": 1},
            "prestige_bands": {"newbie": [0, None]},
            "projection": {"guest_template_keys": [], "gear_template_keys": []},
        }

        response = client.get(reverse("gameplay:map_search_api"), {"type": "region", "region": manor.region})

        assert response.status_code == 200
        assert not BotBackfillDemand.objects.exists()

    def test_region_backfill_request_records_demand_via_post(self, manor_with_user, settings):
        manor, client = manor_with_user
        manor.region = "north"
        manor.newbie_protection_until = None
        manor.peace_shield_until = None
        manor.save(update_fields=["region", "newbie_protection_until", "peace_shield_until"])
        settings.VIRTUAL_PLAYER_CONFIG = {
            "population": {"min_attackable_per_band": 1},
            "prestige_bands": {"newbie": [0, None]},
            "projection": {"guest_template_keys": [], "gear_template_keys": []},
        }

        response = client.post(
            reverse("gameplay:map_backfill_request_api"),
            data=json.dumps({"region": manor.region}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["requested"] is True
        assert BotBackfillDemand.objects.filter(region=manor.region, prestige_band="newbie", needed=1).exists()

    def test_region_backfill_request_records_overseas_region(self, manor_with_user, settings):
        manor, client = manor_with_user
        manor.region = "overseas"
        manor.newbie_protection_until = None
        manor.peace_shield_until = None
        manor.save(update_fields=["region", "newbie_protection_until", "peace_shield_until"])
        settings.VIRTUAL_PLAYER_CONFIG = {
            "population": {"min_attackable_per_band": 1},
            "prestige_bands": {"newbie": [0, None]},
            "projection": {"guest_template_keys": [], "gear_template_keys": []},
        }

        response = client.post(
            reverse("gameplay:map_backfill_request_api"),
            data=json.dumps({"region": "overseas"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.json() == {"success": True, "requested": True}
        assert BotBackfillDemand.objects.filter(region="overseas", prestige_band="newbie", needed=1).exists()

    def test_region_backfill_request_rejects_get(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:map_backfill_request_api"))

        assert response.status_code == 405
        assert not BotBackfillDemand.objects.exists()

    def test_region_backfill_request_rejects_invalid_region(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.post(
            reverse("gameplay:map_backfill_request_api"),
            data=json.dumps({"region": "unknown"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == "地区参数无效"
        assert not BotBackfillDemand.objects.exists()

    def test_map_search_by_name(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:map_search_api"), {"type": "name", "q": "test"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_map_search_negative_page_clamped_to_one(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(
            reverse("gameplay:map_search_api"),
            {"type": "region", "region": manor.region, "page": -5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["page"] == 1

    def test_protection_status_api(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:protection_status_api"))
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "protection" in data

    def test_raid_status_api(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:raid_status_api"))
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "active_raids" in data

    def test_raid_status_api_reads_status_without_triggering_refresh(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"manor": 0, "refresh_activity": 0, "raids": 0, "scouts": 0, "incoming": 0}

        monkeypatch.setattr(
            "gameplay.views.map.get_manor",
            lambda user: calls.__setitem__("manor", calls["manor"] + 1) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.map.refresh_raid_activity",
            lambda *_args, **_kwargs: calls.__setitem__("refresh_activity", calls["refresh_activity"] + 1),
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_active_raids",
            lambda current_manor: calls.__setitem__("raids", calls["raids"] + 1) or [],
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_active_scouts",
            lambda current_manor: calls.__setitem__("scouts", calls["scouts"] + 1) or [],
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_incoming_raids",
            lambda current_manor: calls.__setitem__("incoming", calls["incoming"] + 1) or [],
        )

        response = client.get(reverse("gameplay:raid_status_api"))

        assert response.status_code == 200
        assert calls == {"manor": 1, "refresh_activity": 0, "raids": 1, "scouts": 1, "incoming": 1}

    def test_refresh_raid_activity_api_refreshes_before_listing(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls: list[tuple[object, ...]] = []

        monkeypatch.setattr(
            "gameplay.views.map.get_manor",
            lambda user: calls.append(("manor", user)) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.map.refresh_raid_activity",
            lambda current_manor, *, prefer_async=False: calls.append(
                ("refresh_activity", current_manor, prefer_async)
            ),
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_active_raids",
            lambda current_manor: calls.append(("raids", current_manor)) or [],
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_active_scouts",
            lambda current_manor: calls.append(("scouts", current_manor)) or [],
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_incoming_raids",
            lambda current_manor: calls.append(("incoming", current_manor)) or [],
        )

        response = client.post(reverse("gameplay:refresh_raid_activity_api"))

        assert response.status_code == 200
        assert calls[0][0] == "manor"
        assert calls[1:] == [
            ("refresh_activity", manor, True),
            ("raids", manor),
            ("scouts", manor),
            ("incoming", manor),
        ]
        data = response.json()
        assert data["success"] is True
        assert data["active_raids"] == []
        assert data["active_scouts"] == []
        assert data["incoming_raids"] == []

    def test_refresh_raid_activity_api_rejects_when_action_lock_conflicts(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        called = {"refresh": 0}

        monkeypatch.setattr("gameplay.views.map._acquire_map_action_lock", lambda *_a, **_k: (False, "", None))
        monkeypatch.setattr(
            "gameplay.views.map.refresh_raid_activity",
            lambda *_args, **_kwargs: called.__setitem__("refresh", called["refresh"] + 1),
        )

        response = client.post(reverse("gameplay:refresh_raid_activity_api"))

        assert response.status_code == 409
        payload = response.json()
        assert payload["success"] is False
        assert "请求处理中，请稍候重试" in payload["error"]
        assert called["refresh"] == 0

    def test_refresh_raid_activity_api_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.map.refresh_raid_activity",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy refresh")),
        )

        with pytest.raises(ValueError, match="legacy refresh"):
            client.post(reverse("gameplay:refresh_raid_activity_api"))

    def test_manor_detail_api(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:manor_detail_api", kwargs={"manor_id": manor.id}))
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "manor" in data

    def test_manor_detail_api_not_found(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:manor_detail_api", kwargs={"manor_id": 99999}))
        assert response.status_code == 404
