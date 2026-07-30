from __future__ import annotations

import pytest
from django.urls import reverse

from battle.view_helpers import serialize_city_defense_rows
from core.config import BUILDING_KEYS
from gameplay.services.manor.core import ensure_manor
from tests.battle_report_view.support import create_report


@pytest.mark.django_db
def test_battle_report_context_shows_brief_city_defense_names_under_troops(client, django_user_model):
    user = django_user_model.objects.create_user(username="city_defense_report", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="敌人",
        battle_type="raid",
        defender_city_defenses=[
            {
                "key": BUILDING_KEYS.WALL,
                "name": "城墙",
                "level": 10,
                "hp": 30000,
                "max_hp": 30000,
                "attack": 0,
                "defense": 1500,
            },
            {
                "key": BUILDING_KEYS.ARROW_TOWER,
                "name": "箭塔",
                "level": 4,
                "hp": 2500,
                "max_hp": 6000,
                "attack": 600,
                "defense": 360,
            },
        ],
    )

    assert client.login(username="city_defense_report", password="pass123")
    response = client.get(reverse("battle:report_detail", args=[report.pk]))

    assert response.status_code == 200
    assert response.context["defender_city_defenses"][0]["display_name"] == "崭新的高级城墙"
    assert response.context["defender_city_defenses"][1]["display_name"] == "破烂的中级箭塔"
    assert "城防建筑" in response.content.decode()
    assert "崭新的高级城墙" in response.content.decode()
    assert "破烂的中级箭塔" in response.content.decode()
    assert '<span class="troop-placeholder">城</span>' not in response.content.decode()
    assert '<span class="troop-placeholder">箭</span>' not in response.content.decode()
    assert "血量" not in response.content.decode()
    assert "攻 0 防" not in response.content.decode()
    assert "30000" not in response.content.decode()
    assert "2500" not in response.content.decode()


def test_city_defense_report_names_use_three_qualitative_conditions():
    rows = serialize_city_defense_rows(
        [
            {"key": BUILDING_KEYS.WALL, "name": "城墙", "level": 10, "hp": 30_000, "max_hp": 30_000},
            {"key": BUILDING_KEYS.WALL, "name": "城墙", "level": 6, "hp": 12_000, "max_hp": 18_000},
            {"key": BUILDING_KEYS.ARROW_TOWER, "name": "箭塔", "level": 4, "hp": 2_500, "max_hp": 6_000},
        ]
    )

    assert [row["display_name"] for row in rows] == [
        "崭新的高级城墙",
        "受损的中级城墙",
        "破烂的中级箭塔",
    ]


@pytest.mark.django_db
def test_battle_report_v2_city_defense_still_hides_exact_hp(client, django_user_model):
    user = django_user_model.objects.create_user(username="city_defense_report_v2", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="敌人",
        battle_type="raid",
        defender_city_defenses=[
            {
                "schema_version": 2,
                "key": BUILDING_KEYS.WALL,
                "name": "城墙",
                "level": 6,
                "initial_hp": 17_321,
                "hp": 9_876,
                "max_hp": 18_000,
                "recovered_before_battle": 321,
                "settled_hp": 9_876,
                "destroyed": False,
                "attack": 0,
                "defense": 180,
            }
        ],
    )

    assert client.login(username="city_defense_report_v2", password="pass123")
    response = client.get(reverse("battle:report_detail", args=[report.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "受损的中级城墙" in content
    assert "17321" not in content
    assert "9876" not in content
    assert "18000" not in content


@pytest.mark.django_db
def test_battle_report_city_defense_empty_state_says_none(client, django_user_model):
    user = django_user_model.objects.create_user(username="city_defense_report_empty", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="敌人",
        battle_type="raid",
    )

    assert client.login(username="city_defense_report_empty", password="pass123")
    response = client.get(reverse("battle:report_detail", args=[report.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "未修建城防" not in content
    assert "<li>无</li>" in content
