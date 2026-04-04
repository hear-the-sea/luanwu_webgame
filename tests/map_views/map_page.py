"""
地图页面与配置页测试
"""

import pytest
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
class TestMapViews:
    def test_map_page(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:map"))
        assert response.status_code == 200
        assert "regions" in response.context

    def test_map_page_exposes_only_four_continents_and_overseas(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:map"))

        assert response.status_code == 200
        assert response.context["regions"] == [
            ("north", "北俱芦洲"),
            ("east", "东胜神洲"),
            ("west", "西牛贺洲"),
            ("south", "南赡部洲"),
            ("overseas", "化外之地"),
        ]

    def test_map_page_loads_external_page_script_without_inline_logic(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:map"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/map-page.js" in body
        assert f'data-map-api-base="{reverse("gameplay:map_search_api")}"' in body
        assert f'data-scout-api-url="{reverse("gameplay:start_scout_api")}"' in body
        assert f'data-raid-config-url-prefix="{reverse("gameplay:map")}raid/"' in body
        assert "const mapApiBase =" not in body
        assert "window.startScout = startScout" not in body
        assert "fetch('/manor/api/map/raid/'" not in body

    def test_map_page_syncs_resources_before_loading_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"prepared": 0, "context": 0}

        def _fake_context(*_args, **_kwargs):
            calls["context"] += 1
            return {
                "manor": manor,
                "selected_region": manor.region,
                "search_query": "",
                "protection_status": {},
                "active_raids": [],
                "active_scouts": [],
                "incoming_raids": [],
                "scout_count": 0,
                "player_troops": [],
            }

        monkeypatch.setattr(
            "gameplay.views.map.get_prepared_manor_for_read",
            lambda request, **_kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr("gameplay.views.map.get_map_context", _fake_context)

        response = client.get(reverse("gameplay:map"))
        assert response.status_code == 200
        assert calls == {"prepared": 1, "context": 1}

    def test_map_region_filter(self, manor_with_user):
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:map") + "?region=north")
        assert response.status_code == 200
        assert response.context["selected_region"] == "north"

    def test_map_page_renders_selected_region_display_name(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:map") + "?region=north")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "北俱芦洲地区的庄园" in body
        assert "north地区的庄园" not in body

    def test_raid_config_page_loads_external_page_script_without_inline_logic(
        self,
        manor_with_user,
        monkeypatch,
        django_user_model,
    ):
        manor, client = manor_with_user
        target_user = django_user_model.objects.create_user(username="raid_config_target", password="pass123")
        target_manor = ensure_manor(target_user)

        monkeypatch.setattr(
            "gameplay.views.map.get_prepared_manor_for_read",
            lambda request, **_kwargs: manor,
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_raid_config_context",
            lambda current_manor, current_target: {
                "manor": current_manor,
                "target_manor": current_target,
                "target_info": {
                    "region_display": current_target.region_display,
                    "prestige": 10,
                    "prestige_comparison": "lower",
                    "distance": 1.0,
                    "travel_time": 30,
                    "is_protected": False,
                },
                "can_attack": True,
                "attack_reason": "",
                "available_guests": [],
                "player_troops": [],
                "max_squad_size": 3,
            },
        )

        response = client.get(reverse("gameplay:raid_config", kwargs={"target_id": target_manor.id}))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/raid-config-page.js" in body
        assert f'data-raid-api-url="{reverse("gameplay:start_raid_api")}"' in body
        assert f'data-map-url="{reverse("gameplay:map")}"' in body
        assert "const raidApiUrl =" not in body
        assert "fetch(raidApiUrl" not in body
