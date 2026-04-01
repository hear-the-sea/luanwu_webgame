from __future__ import annotations

import re

import pytest
from django.db import DatabaseError
from django.urls import reverse

from gameplay.models import MissionTemplate
from guests.models import GuestTemplate


@pytest.mark.django_db
class TestTaskBoardPage:
    def test_task_board_page(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:tasks"))
        assert response.status_code == 200
        assert "missions" in response.context

    def test_task_board_page_loads_external_page_script_without_inline_logic(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/tasks-page.js" in body
        assert "const maxSquadSize" not in body

    def test_task_board_tolerates_resource_sync_error(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.mission_page_context.project_manor_activity_for_read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("sync failed")),
        )

        response = client.get(reverse("gameplay:tasks"))
        assert response.status_code == 200
        assert "missions" in response.context

    def test_task_board_with_mission_selected(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:tasks") + "?mission=huashan_lunjian")
        assert response.status_code == 200

    def test_task_board_selects_matching_difficulty_tab_for_selected_mission(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_selected_intermediate",
            name="指定中级任务",
            difficulty=MissionTemplate.Difficulty.INTERMEDIATE,
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert '<button class="tw-trade-tab active" data-tab="intermediate">中级任务</button>' in body
        assert '<div id="tab-intermediate" class="mission-tab-content active">' in body

    def test_task_board_mission_names_link_to_details_for_every_difficulty(self, manor_with_user):
        _manor, client = manor_with_user
        missions = [
            ("task_board_name_link_junior", "名称链接初级", "junior"),
            ("task_board_name_link_intermediate", "名称链接中级", "intermediate"),
            ("task_board_name_link_advanced", "名称链接高级", "advanced"),
        ]

        for key, name, difficulty in missions:
            MissionTemplate.objects.create(
                key=key,
                name=name,
                difficulty=difficulty,
                daily_limit=3,
            )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        for key, name, _difficulty in missions:
            pattern = rf'<a[^>]+href="\?mission={key}"[^>]*>{name}</a>'
            assert re.search(pattern, body)

    def test_task_board_mission_name_links_do_not_render_underlines(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_name_link_no_underline",
            name="名称无下划线",
            difficulty="junior",
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        pattern = (
            rf'<a[^>]+class="[^"]*no-underline[^"]*hover:no-underline[^"]*"[^>]+'
            rf'href="\?mission={mission.key}"[^>]*>{mission.name}</a>'
        )
        assert re.search(pattern, body)

    def test_task_board_selected_mission_hides_enemy_guest_names_below_cards(
        self,
        manor_with_user,
    ):
        _manor, client = manor_with_user
        GuestTemplate.objects.create(
            key="task_board_enemy_gray",
            name="灰阶敌将",
            archetype="military",
            rarity="gray",
        )
        GuestTemplate.objects.create(
            key="task_board_enemy_black",
            name="黑阶敌将",
            archetype="civil",
            rarity="black",
        )
        MissionTemplate.objects.create(
            key="task_board_enemy_rarity",
            name="测试敌方颜色",
            enemy_guests=[
                {"key": "task_board_enemy_gray", "label": "灰阶敌将"},
                {"key": "task_board_enemy_black", "label": "黑阶敌将"},
            ],
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + "?mission=task_board_enemy_rarity")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert body.count("tw-enemy-entry") >= 2
        assert "tw-enemy-meta" not in body
        assert '<span class="tw-guest-name-sm rarity-text-gray">灰阶敌将</span>' not in body
        assert '<span class="tw-guest-name-sm rarity-text-black">黑阶敌将</span>' not in body

    def test_task_board_selected_high_end_mission_hides_elite_enemy_name_row(
        self,
        manor_with_user,
        mission_templates,
    ):
        _manor, client = manor_with_user
        GuestTemplate.objects.update_or_create(
            key="task_barbarian_chanyu",
            defaults={
                "name": "蛮族单于",
                "archetype": "military",
                "rarity": "orange",
            },
        )

        response = client.get(reverse("gameplay:tasks") + "?mission=manzu_ruqin")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "单于" in body
        assert "tw-enemy-entry" in body
        assert "tw-enemy-meta" not in body
