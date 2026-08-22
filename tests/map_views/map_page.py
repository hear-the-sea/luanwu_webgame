"""
地图页面与配置页测试
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from gameplay.models import BotProfile
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
        assert f'data-map-backfill-api-url="{reverse("gameplay:map_backfill_request_api")}"' in body
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

    def test_map_page_raid_scout_countdowns_use_explicit_refresh_api(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        now = timezone.now()

        monkeypatch.setattr(
            "gameplay.views.map.get_prepared_manor_for_read",
            lambda request, **_kwargs: manor,
        )
        monkeypatch.setattr(
            "gameplay.views.map.get_map_context",
            lambda _manor, selected_region, search_query: {
                "manor": _manor,
                "selected_region": selected_region,
                "search_query": search_query,
                "protection_status": {},
                "active_raids": [
                    SimpleNamespace(
                        id=12,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="marching",
                        next_state_at=now + timedelta(minutes=5),
                        get_status_display="行军中",
                    )
                ],
                "active_scouts": [
                    SimpleNamespace(
                        id=11,
                        defender=SimpleNamespace(display_name="侦察目标"),
                        status="scouting",
                        next_state_at=now + timedelta(minutes=3),
                        get_status_display="侦察中",
                    )
                ],
                "incoming_raids": [
                    SimpleNamespace(
                        id=13,
                        attacker=SimpleNamespace(display_name="来袭者"),
                        arrive_at=now + timedelta(minutes=7),
                    )
                ],
                "scout_count": 2,
                "player_troops": [],
            },
        )

        response = client.get(reverse("gameplay:map"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        refresh_url = reverse("gameplay:refresh_raid_activity_api")
        assert body.count(f'data-refresh-url="{refresh_url}"') == 3
        assert body.count('data-refresh-method="post"') == 3
        assert "当前战况" in body
        assert "踢馆：目标庄园" in body
        assert "侦察：侦察目标" in body
        assert "来袭：来袭者" in body

    def test_raid_config_page_loads_external_page_script_without_inline_logic(
        self,
        manor_with_user,
        monkeypatch,
        django_user_model,
    ):
        manor, client = manor_with_user
        target_user = django_user_model.objects.create_user(username="raid_config_target", password="pass123")
        target_manor = ensure_manor(target_user)
        now = timezone.now()
        BotProfile.objects.create(
            manor=target_manor,
            state=BotProfile.State.RETIRED,
            prestige_band="newbie",
            target_prestige_band="newbie",
            current_prestige_band="newbie",
            growth_seed=target_manor.id,
            next_growth_at=now,
            abandon_at=now,
            retire_at=now,
            maintenance_stopped_at=now,
        )

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
                    "prestige": 987654,
                    "prestige_comparison": "lower",
                    "distance": 1.0,
                    "travel_time": 30,
                    "is_protected": False,
                },
                "can_attack": True,
                "attack_reason": "",
                "available_guests": [
                    SimpleNamespace(
                        id=7,
                        display_name="测试门客",
                        level=12,
                        current_hp=80,
                        max_hp=100,
                        troop_capacity=230,
                        template=SimpleNamespace(avatar=None),
                    )
                ],
                "player_troops": [
                    {
                        "key": "guard",
                        "name": "测试护院",
                        "count": 320,
                        "avatar": "",
                    }
                ],
                "max_squad_size": 3,
            },
        )

        response = client.get(reverse("gameplay:raid_config", kwargs={"target_id": target_manor.id}))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "<dt>声望</dt>" not in body
        assert "987654" not in body
        assert "data-raid-config-page" in body
        assert 'class="tw-panel tw-raid-intel"' in body
        assert 'class="tw-raid-loadout-grid"' in body
        assert 'data-troop-capacity="230"' in body
        assert "data-raid-select-max" in body
        assert "data-raid-clear-guests" in body
        assert "data-raid-clear-troops" in body
        assert "data-raid-capacity-status" in body
        assert "js/raid-config-page.js" in body
        assert f'data-raid-api-url="{reverse("gameplay:start_raid_api")}"' in body
        assert f'data-map-url="{reverse("gameplay:map")}"' in body
        assert "const raidApiUrl =" not in body
        assert "fetch(raidApiUrl" not in body

    def test_raid_config_page_redirects_when_attack_is_blocked(self, manor_with_user, monkeypatch, django_user_model):
        manor, client = manor_with_user
        target_user = django_user_model.objects.create_user(username="raid_config_blocked_target", password="pass123")
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
                "target_info": {},
                "can_attack": False,
                "attack_reason": "对方声望过高，无法攻击",
            },
        )

        response = client.get(reverse("gameplay:raid_config", kwargs={"target_id": target_manor.id}))

        assert response.status_code == 302
        assert response.url == reverse("gameplay:map")
        assert [str(message) for message in response.wsgi_request._messages] == ["无法进攻：对方声望过高，无法攻击"]

    def test_raid_config_page_hides_stale_virtual_player(self, manor_with_user, django_user_model):
        _manor, client = manor_with_user
        target_user = django_user_model.objects.create_user(username="raid_config_stale_target", password="pass123")
        target_manor = ensure_manor(target_user)
        now = timezone.now()
        BotProfile.objects.create(
            manor=target_manor,
            state=BotProfile.State.STALE,
            prestige_band="newbie",
            target_prestige_band="newbie",
            current_prestige_band="newbie",
            growth_seed=target_manor.id,
            next_growth_at=now,
            abandon_at=now,
            retire_at=now,
            maintenance_stopped_at=now,
        )

        response = client.get(reverse("gameplay:raid_config", kwargs={"target_id": target_manor.id}))

        assert response.status_code == 404

    def test_map_visibility_queryset_keeps_real_player(self, django_user_model):
        from gameplay.views.map import _map_visible_manors

        target_user = django_user_model.objects.create_user(username="map_visible_real_target", password="pass123")
        target_manor = ensure_manor(target_user)

        assert _map_visible_manors().filter(pk=target_manor.pk).exists()
