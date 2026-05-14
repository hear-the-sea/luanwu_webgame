from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from core.exceptions import GameError
from gameplay.constants import BUILDING_MAX_LEVELS
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
class TestCoreViews:
    """核心页面视图测试"""

    def test_home_page_anonymous(self, client):
        """匿名用户访问首页"""
        response = client.get(reverse("home"))
        assert response.status_code == 200

    def test_home_page_authenticated(self, authenticated_client):
        """登录用户访问首页"""
        ensure_manor(authenticated_client.user)
        response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200
        assert "manor" in response.context

    def test_home_page_syncs_resources_before_loading_context(self, authenticated_client, monkeypatch):
        ensure_manor(authenticated_client.user)
        calls = {"prepared": 0, "context": 0}
        manor = ensure_manor(authenticated_client.user)

        def _fake_context(manor):
            calls["context"] += 1
            return {"manor": manor}

        monkeypatch.setattr(
            "gameplay.views.core.get_prepared_manor_for_read",
            lambda request, **_kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr("gameplay.views.core.get_home_context", _fake_context)

        response = authenticated_client.get(reverse("home"))
        assert response.status_code == 200
        assert calls == {"prepared": 1, "context": 1}

    def test_home_page_raid_scout_countdowns_use_explicit_refresh_api(self, authenticated_client, monkeypatch):
        manor = ensure_manor(authenticated_client.user)
        now = timezone.now()

        monkeypatch.setattr(
            "gameplay.views.core.get_prepared_manor_for_read",
            lambda request, **_kwargs: manor,
        )
        monkeypatch.setattr(
            "gameplay.views.core.get_home_context",
            lambda _manor: {
                "manor": manor,
                "resources": [],
                "resource_labels": {},
                "guests": [],
                "guest_count": 0,
                "active_runs": [],
                "upgrading_buildings": [],
                "upgrading_technologies": [],
                "total_guest_salary": 0,
                "building_income": [],
                "grain_production": 0,
                "personnel_grain_cost": 0,
                "player_troops": [],
                "active_scouts": [
                    SimpleNamespace(
                        id=11,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="scouting",
                        next_state_at=now + timedelta(minutes=3),
                        get_status_display="侦察中",
                    )
                ],
                "active_raids": [
                    SimpleNamespace(
                        id=12,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="marching",
                        next_state_at=now + timedelta(minutes=5),
                        get_status_display="行军中",
                        can_retreat=False,
                        is_retreating=False,
                    )
                ],
                "incoming_raids": [
                    SimpleNamespace(
                        id=13,
                        attacker=SimpleNamespace(display_name="来袭者", location_display="齐国 临淄"),
                        arrive_at=now + timedelta(minutes=7),
                    )
                ],
            },
        )

        response = authenticated_client.get(reverse("home"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        refresh_url = reverse("gameplay:refresh_raid_activity_api")
        assert body.count(f'data-refresh-url="{refresh_url}"') == 3
        assert body.count('data-refresh-method="post"') == 3

    def test_home_page_all_refreshing_countdowns_expose_explicit_refresh_or_check_endpoint(
        self,
        authenticated_client,
        monkeypatch,
    ):
        manor = ensure_manor(authenticated_client.user)
        now = timezone.now()

        monkeypatch.setattr(
            "gameplay.views.core.get_prepared_manor_for_read",
            lambda request, **_kwargs: manor,
        )
        monkeypatch.setattr(
            "gameplay.views.core.get_home_context",
            lambda _manor: {
                "manor": manor,
                "resources": [],
                "resource_labels": {},
                "guests": [],
                "guest_count": 0,
                "total_guest_salary": 0,
                "building_income": [],
                "grain_production": 0,
                "personnel_grain_cost": 0,
                "player_troops": [],
                "active_scouts": [
                    SimpleNamespace(
                        id=11,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="scouting",
                        next_state_at=now + timedelta(minutes=3),
                        get_status_display="侦察中",
                    )
                ],
                "active_raids": [
                    SimpleNamespace(
                        id=12,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="marching",
                        next_state_at=now + timedelta(minutes=5),
                        get_status_display="行军中",
                        can_retreat=False,
                        is_retreating=False,
                    )
                ],
                "incoming_raids": [
                    SimpleNamespace(
                        id=13,
                        attacker=SimpleNamespace(display_name="来袭者", location_display="齐国 临淄"),
                        arrive_at=now + timedelta(minutes=7),
                    )
                ],
                "active_guild_pvp_run": SimpleNamespace(
                    run=SimpleNamespace(id=21, defender_guild=SimpleNamespace(name="敌方帮会")),
                    display_hint="正在向敌方帮会进军",
                    display_eta_at=now + timedelta(minutes=6),
                    display_eta_label="到达",
                    can_retreat=True,
                    action_label="撤回",
                ),
                "incoming_guild_pvp_runs": [
                    SimpleNamespace(
                        run=SimpleNamespace(attacker_guild=SimpleNamespace(name="来袭帮会")),
                        display_hint="敌方帮会正在向本帮进军",
                        display_eta_at=now + timedelta(minutes=8),
                        display_eta_label="到达",
                    )
                ],
                "active_guild_mission": SimpleNamespace(
                    id=31,
                    template=SimpleNamespace(name="帮会巡防"),
                    return_at=now + timedelta(minutes=4),
                    can_retreat_from_home=False,
                ),
                "active_runs": [
                    SimpleNamespace(
                        id=41,
                        mission=SimpleNamespace(name="普通出征", is_defense=False),
                        is_retreating=False,
                        is_returning=False,
                        next_state_at=now + timedelta(minutes=9),
                        can_retreat=True,
                    )
                ],
                "upgrading_buildings": [
                    SimpleNamespace(
                        level=1,
                        upgrade_complete_at=now + timedelta(minutes=2),
                        building_type=SimpleNamespace(name="农田"),
                    )
                ],
                "upgrading_technologies": [
                    SimpleNamespace(
                        level=2,
                        upgrade_complete_at=now + timedelta(minutes=10),
                        display_name="刀兵攻击",
                        tech_key="dao_attack",
                    )
                ],
            },
        )

        response = authenticated_client.get(reverse("home"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        countdown_tags = re.findall(r"<span class=\"countdown\".*?>", body, flags=re.DOTALL)
        refreshing_countdowns = [tag for tag in countdown_tags if 'data-refresh="1"' in tag]
        assert refreshing_countdowns
        for tag in refreshing_countdowns:
            assert 'data-refresh-url="' in tag or 'data-check-url="' in tag
        assert reverse("gameplay:refresh_raid_activity_api") in body
        assert reverse("gameplay:refresh_mission_runs_api") in body
        assert reverse("gameplay:refresh_building_upgrades_api") in body
        assert reverse("gameplay:refresh_technology_upgrades_api") in body
        assert reverse("guilds:refresh_pvp_activity_api") in body
        assert reverse("guilds:refresh_mission_runs_api") in body

    def test_home_page_uses_external_landing_script_for_retreat_and_collapse_actions(
        self,
        authenticated_client,
        monkeypatch,
    ):
        manor = ensure_manor(authenticated_client.user)
        now = timezone.now()

        monkeypatch.setattr(
            "gameplay.views.core.get_prepared_manor_for_read",
            lambda request, **_kwargs: manor,
        )
        monkeypatch.setattr(
            "gameplay.views.core.get_home_context",
            lambda _manor: {
                "manor": manor,
                "resources": [],
                "resource_labels": {},
                "guests": [],
                "guest_count": 0,
                "active_runs": [],
                "upgrading_buildings": [],
                "upgrading_technologies": [],
                "total_guest_salary": 0,
                "building_income": [],
                "grain_production": 0,
                "personnel_grain_cost": 0,
                "player_troops": [],
                "incoming_raids": [],
                "active_scouts": [
                    SimpleNamespace(
                        id=11,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="scouting",
                        next_state_at=now + timedelta(minutes=3),
                        get_status_display="侦察中",
                    )
                ],
                "active_raids": [
                    SimpleNamespace(
                        id=12,
                        defender=SimpleNamespace(display_name="目标庄园"),
                        status="marching",
                        next_state_at=now + timedelta(minutes=5),
                        get_status_display="行军中",
                        can_retreat=True,
                        is_retreating=False,
                    )
                ],
            },
        )

        response = authenticated_client.get(reverse("home"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "css/landing-page.css" in body
        assert "js/landing-page.js" in body
        assert 'data-retreat-url="/manor/api/map/raid/12/retreat/"' in body
        assert "<style>" not in body
        assert "manor-collapse-states" not in body
        assert "document.querySelectorAll('.scout-retreat-form')" not in body
        assert "fetch('/manor/api/map/raid/'" not in body

    def test_dashboard_requires_login(self, client):
        """仪表盘需要登录"""
        response = client.get(reverse("gameplay:dashboard"))
        assert response.status_code == 302

    def test_dashboard_authenticated(self, manor_with_user):
        """登录用户访问仪表盘"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:dashboard"))
        assert response.status_code == 200
        assert "buildings" in response.context

    def test_dashboard_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr("gameplay.views.core.get_prepared_manor_for_read", lambda request, **kwargs: manor)
        from gameplay.selectors.core import get_dashboard_context as real_get_dashboard_context

        def _fake_selector(current_manor, *, category):
            calls["selector"] += 1
            assert current_manor is manor
            return real_get_dashboard_context(current_manor, category=category)

        monkeypatch.setattr("gameplay.views.core.get_dashboard_context", _fake_selector)

        response = client.get(reverse("gameplay:dashboard"))

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert "buildings" in response.context

    def test_dashboard_refresh_database_error_does_not_500(self, manor_with_user, monkeypatch):
        """数据库故障时仪表盘应静默降级而不是返回500。"""
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.core.project_resource_production_for_read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.get(reverse("gameplay:dashboard"))
        assert response.status_code == 200
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        assert messages == []

    def test_dashboard_refresh_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        """编程错误不应被页面层吞掉。"""
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.core.project_resource_production_for_read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.get(reverse("gameplay:dashboard"))

    def test_authenticated_layout_contains_partial_nav_markers(self, manor_with_user):
        """认证页面应包含局部导航刷新容器与脚本。"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:dashboard"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'id="main-nav"' in body
        assert 'id="info-bar"' in body
        assert 'id="page-shell"' in body
        assert 'id="page-extra-scripts"' in body
        assert "js/nav_partial.js" in body
        assert body.count('data-partial-nav="1"') >= 12
        assert f'href="{reverse("gameplay:warehouse")}" data-partial-nav="1"' in body
        assert f'href="{reverse("trade:trade")}" data-partial-nav="1"' in body

    def test_authenticated_pages_use_a_fixed_browser_title(self, manor_with_user):
        """认证页面的浏览器标题应固定为游戏名。"""
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:dashboard"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "<title>春秋乱世庄园主</title>" in body

    def test_guest_layout_uses_shared_class_instead_of_inline_style(self, client):
        response = client.get(reverse("accounts:login"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'class="content-area content-area--guest"' in body
        assert (
            'style="padding: 20px; min-height: 400px; display: flex; align-items: center; justify-content: center;"'
            not in body
        )

    def test_authenticated_home_menu_uses_shared_classes_instead_of_inline_styles(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("home"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'class="nav-tab nav-tab--messages ' in body
        assert 'style="position: relative;"' not in body
        assert 'style="display: contents;"' not in body
        assert 'style="width: 100%; border: 1px solid #5A2D0C; cursor: pointer;"' not in body

    def test_home_sidebar_uses_real_links_and_no_placeholder_values(self, manor_with_user):
        """首页侧栏只展示真实入口，不再保留伪链接或硬编码业务值。"""
        _manor, client = manor_with_user

        response = client.get(reverse("home"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'href="#"' not in body
        assert "1000/1000" not in body
        assert "贡献积分" not in body
        assert "当前贡献" in body
        assert "未加入帮会" in body
        assert f'href="{reverse("accounts:profile")}" class="btn-menu"' in body
        assert f'href="{reverse("gameplay:ranking")}" class="btn-menu"' in body
        assert f'href="{reverse("gameplay:guide")}" class="btn-menu"' in body
        assert f'href="{reverse("gameplay:messages")}" class="btn-menu"' in body
        assert f'href="{reverse("gameplay:settings")}" class="btn-menu"' in body

    def test_guide_page_renders_beginner_tips_with_shared_layout(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:guide"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "游戏攻略" in body
        assert "粮食主要靠农田" in body
        assert "门客去聚贤庄招募" in body
        assert "装备可以从打工宝箱" in body
        assert 'class="dashboard"' in body
        assert 'class="tw-panel' in body
        assert "guide-card" not in body

    def test_authenticated_layout_loads_chat_widget_modules_in_order(self, manor_with_user):
        """聊天挂件脚本应按 core -> renderer -> layout -> connection -> main 的顺序加载。"""
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:dashboard"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        core_script = "js/chat_widget_core.js"
        renderer_script = "js/chat_widget_renderer.js"
        layout_script = "js/chat_widget_layout.js"
        connection_script = "js/chat_widget_connection.js"
        main_script = "js/chat_widget.js"
        assert core_script in body
        assert renderer_script in body
        assert layout_script in body
        assert connection_script in body
        assert main_script in body
        assert body.index(core_script) < body.index(renderer_script) < body.index(layout_script)
        assert body.index(layout_script) < body.index(connection_script) < body.index(main_script)

    def test_dashboard_upgrading_building_has_auto_refresh_countdown(self, manor_with_user):
        """建筑升级倒计时应携带自动刷新标记。"""
        manor, client = manor_with_user
        building = manor.buildings.select_related("building_type").first()
        assert building is not None
        building.is_upgrading = True
        building.upgrade_complete_at = timezone.now() + timezone.timedelta(minutes=5)
        building.save(update_fields=["is_upgrading", "upgrade_complete_at"])

        response = client.get(
            reverse(
                "gameplay:buildings_category",
                kwargs={"category": building.building_type.category},
            )
        )
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'data-countdown="' in body
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_building_upgrades_api") in body
        assert 'data-refresh-method="post"' in body

    def test_dashboard_max_level_building_shows_maxed_state(self, manor_with_user):
        """达到建筑等级上限时，页面应显示满级、禁用升级按钮，并将升级消耗显示为 /。"""
        manor, client = manor_with_user
        building = manor.buildings.select_related("building_type").get(building_type__key="juxianzhuang")
        max_level = BUILDING_MAX_LEVELS[building.building_type.key]
        building.level = max_level
        building.is_upgrading = False
        building.upgrade_complete_at = None
        building.save(update_fields=["level", "is_upgrading", "upgrade_complete_at"])

        response = client.get(
            reverse(
                "gameplay:buildings_category",
                kwargs={"category": building.building_type.category},
            )
        )
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert f"Lv {max_level} / {max_level} 满级" in body
        assert "<span>/</span>" in body
        assert "disabled>已满级</button>" in body

    def test_settings_page(self, manor_with_user):
        """设置页面"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:settings"))
        assert response.status_code == 200

    def test_settings_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr(
            "gameplay.views.core.get_settings_page_context",
            lambda current_manor: calls.__setitem__("selector", calls["selector"] + 1) or {"rename_card_count": 9},
        )

        response = client.get(reverse("gameplay:settings"))

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert response.context["rename_card_count"] == 9

    def test_rename_manor_known_error_shows_message(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.core.rename_manor",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(GameError("rename blocked")),
        )

        response = client.post(reverse("gameplay:rename_manor"), {"new_name": "新庄园名"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:settings")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("rename blocked" in m for m in messages)

    def test_rename_manor_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.core.rename_manor",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy rename blocked")),
        )

        with pytest.raises(ValueError, match="legacy rename blocked"):
            client.post(reverse("gameplay:rename_manor"), {"new_name": "新庄园名"})

    def test_rename_manor_database_error_does_not_500(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.core.rename_manor",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:rename_manor"), {"new_name": "新庄园名"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:settings")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_rename_manor_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.core.rename_manor",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:rename_manor"), {"new_name": "新庄园名"})

    def test_ranking_page(self, manor_with_user):
        """排行榜页面"""
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:ranking"))
        assert response.status_code == 200
        assert "ranking" in response.context

    def test_ranking_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}
        from gameplay.selectors.core import get_ranking_page_context as real_get_ranking_page_context

        def _fake_selector(current_manor):
            calls["selector"] += 1
            assert current_manor.pk == manor.pk
            return real_get_ranking_page_context(current_manor)

        monkeypatch.setattr("gameplay.views.core.get_ranking_page_context", _fake_selector)

        response = client.get(reverse("gameplay:ranking"))

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert "ranking" in response.context
