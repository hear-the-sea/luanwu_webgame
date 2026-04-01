from __future__ import annotations

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

    def test_task_board_selected_mission_shows_enemy_guest_rarity_classes(
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
        assert "灰阶敌将" in body
        assert "rarity-text-gray" in body
        assert "黑阶敌将" in body
        assert "rarity-text-black" in body

    def test_task_board_selected_high_end_mission_uses_elite_enemy_rarity(
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
        assert "rarity-text-orange" in body
