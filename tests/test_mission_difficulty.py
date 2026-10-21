"""正式模拟证据、评级覆盖和个人任务页面的集成契约。"""

import json
from pathlib import Path

import pytest
import yaml
from django.urls import reverse

from gameplay.models import MissionTemplate
from gameplay.services.mission_difficulty import load_mission_difficulty_ratings
from guests.templatetags.guest_extras import attribute_icons
from scripts.analysis.rate_mission_difficulty import calculate_difficulty_score, describe_difficulty

ROOT = Path(__file__).resolve().parents[1]
VALUES = (1, 2, 3, 4, 8, 12, 16, 32, 48, 64, 128)


def test_current_ratings_cover_all_43_missions_and_zhanlong_difficulty_increases():
    payload = json.loads((ROOT / "data/mission_difficulty_ratings.json").read_text())
    ratings = payload["ratings"]
    missions = yaml.safe_load((ROOT / "data/mission_templates.yaml").read_text())["missions"]
    assert len(ratings) == len(missions) == 43
    assert set(ratings) == {mission["key"] for mission in missions}
    keys = (
        "zhanlong_yanshan_shizi",
        "zhanlong_changban_tuwei",
        "zhanlong_changban_duanqiao",
        "zhanlong_wuhu_shizhen",
    )
    scores = [ratings[key]["value"] for key in keys]
    assert 1 <= scores[0] < scores[1] < scores[2] < scores[3] <= 128


def test_every_mission_rating_is_backed_by_complete_formal_simulations():
    missions = yaml.safe_load((ROOT / "data/mission_templates.yaml").read_text())["missions"]
    ratings = load_mission_difficulty_ratings()
    report = json.loads((ROOT / "reports/mission_difficulty_simulation_20261021.json").read_text())
    assert set(ratings) == {mission["key"] for mission in missions}
    assert all(1 <= rating["value"] <= 128 for rating in ratings.values())
    assert any(rating["value"] not in VALUES for rating in ratings.values())
    assert min(rating["value"] for rating in ratings.values()) == 1
    assert max(rating["value"] for rating in ratings.values()) == 128
    assert report["total_battles"] == len(missions) * 11 * 40
    for row in report["rows"]:
        rating = ratings[row["key"]]
        if rating.get("simulation_report"):
            latest = json.loads((ROOT / rating["simulation_report"]).read_text())
            row = next(latest_row for latest_row in latest["rows"] if latest_row["key"] == row["key"])
            assert latest["seed_start"] == rating["seed_start"]
        expected_trials = rating.get("seeds_per_profile", 40)
        assert len(row["measurements"]) == 11
        assert all(cell["trials"] == expected_trials for cell in row["measurements"])
        passed = next((cell for cell in row["measurements"] if cell["win_rate"] >= 0.7), None)
        expected_profile = passed["profile"] if passed else 11
        score = ratings[row["key"]]["value"]
        assert score == calculate_difficulty_score(row["measurements"])
        assert score <= VALUES[expected_profile - 1]
        assert expected_profile == 1 or score > VALUES[expected_profile - 2]


@pytest.mark.parametrize("value", range(1, 129))
def test_difficulty_icons_use_base_four_with_mixed_digits(value):
    html = str(attribute_icons(value))
    counts = {icon: html.count(f'attr-{icon}"') for icon in ("star", "moon", "sun", "crown")}
    assert (
        sum(counts[icon] * weight for icon, weight in (("star", 1), ("moon", 4), ("sun", 16), ("crown", 64))) == value
    )
    assert all(counts[icon] <= 3 for icon in ("star", "moon", "sun"))
    assert counts["crown"] <= 2


@pytest.mark.parametrize(
    "wins,expected",
    [
        ([40] * 11, 1),
        ([0] * 11, 128),
        ([0] * 4 + [40] * 7, 7),
        ([0] * 7 + [40] * 4, 28),
        ([0] * 9 + [20, 40], 90),
    ],
)
def test_simulation_win_rates_map_to_interpolated_scores(wins, expected):
    cells = [{"wins": count, "trials": 40} for count in wins]
    assert calculate_difficulty_score(cells) == expected


def test_mixed_difficulty_label_matches_sprite_weights():
    assert describe_difficulty(21) == "1个太阳＋1个月亮＋1颗星"
    assert describe_difficulty(85) == "1个皇冠＋1个太阳＋1个月亮＋1颗星"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "key,difficulty,tab",
    [
        ("jingyanggang", "junior", "junior"),
        ("jiguanshou_chuxian", "intermediate", "intermediate"),
        ("zhuolu_zhongyuan", "advanced", "advanced"),
        ("zhanlong_wuhu_shizhen", "advanced", "series"),
        ("guild_personal_zhenlong_qipu", "junior", "guild"),
    ],
)
def test_task_board_attaches_simulated_rating_in_every_category(manor_with_user, key, difficulty, tab):
    _manor, client = manor_with_user
    MissionTemplate.objects.update_or_create(
        key=key, defaults={"name": "难度展示演练", "difficulty": difficulty, "available_weekdays": []}
    )
    response = client.get(reverse("gameplay:tasks"), {"tab": tab})
    assert response.status_code == 200
    context_key = "guild_personal_mission_data" if tab == "guild" else "mission_data"
    item = next(item for item in response.context[context_key] if item["mission"].key == key)
    rating = load_mission_difficulty_ratings()[key]
    assert item["difficulty_rating"] == rating
    body = response.content.decode()
    assert f'aria-label="任务难度 {rating["value"]}"' in body
    assert "data-task-difficulty" not in body
    assert 'class="tw-mission-difficulty-col text-center">任务难度' in body
    assert "css/tasks-difficulty.css" in body
    assert "js/tasks-difficulty.js" not in body


@pytest.mark.django_db
def test_unknown_task_displays_pending_rating_without_inventing_difficulty(manor_with_user):
    _manor, client = manor_with_user
    mission = MissionTemplate.objects.create(key="new_unrated_mission", name="尚未模拟的任务")
    response = client.get(reverse("gameplay:tasks"))
    assert response.status_code == 200
    item = next(item for item in response.context["mission_data"] if item["mission"].key == mission.key)
    assert item["difficulty_rating"] is None
    assert "待评估" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("difficulty", ["junior", "intermediate", "advanced"])
def test_normal_task_categories_sort_by_score_unless_order_is_explicit(manor_with_user, monkeypatch, difficulty):
    _manor, client = manor_with_user
    keys = [f"sort_{difficulty}_{suffix}" for suffix in ("hard", "easy", "tie", "fixed", "unrated")]
    for key in keys:
        MissionTemplate.objects.create(
            key=key, name=key, difficulty=difficulty, display_order=1 if key.endswith("fixed") else 1000
        )
    monkeypatch.setattr(
        "gameplay.views.mission_page_context.load_mission_difficulty_ratings",
        lambda: {key: {"value": value} for key, value in zip(keys, (128, 1, 1, 128))},
    )
    response = client.get(reverse("gameplay:tasks"), {"tab": difficulty})
    assert response.status_code == 200
    actual = [item["mission"].key for item in response.context["mission_data"] if item["mission"].key in keys]
    assert actual == [keys[3], keys[1], keys[2], keys[0], keys[4]]


@pytest.mark.django_db
def test_order_reset_migration_only_changes_intermediate_and_advanced_regular_tasks():
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    rules = import_module("gameplay.migrations.0239_clear_regular_mission_order")
    cases = [
        ("wagangzhai_nixi", "intermediate", 7, 1000),
        ("xisui_yougu", "intermediate", 9, 1000),
        ("yijin_mijing", "advanced", 9, 1000),
        ("huashan_lunjian_advanced", "advanced", 1100, 1000),
        ("jingyanggang", "junior", 1, 1),
        ("sishui_huaxiong_chengwei", "advanced", 1001, 1001),
        ("guild_personal_shaolin_night_shadow", "intermediate", 1302, 1302),
    ]
    for key, difficulty, original_order, _expected in cases:
        MissionTemplate.objects.update_or_create(
            key=key, defaults={"name": key, "difficulty": difficulty, "display_order": original_order}
        )
    for _ in range(2):
        rules.clear_regular_mission_order(apps, SimpleNamespace(connection=connection))
    for key, _difficulty, _original, expected in cases:
        assert MissionTemplate.objects.get(key=key).display_order == expected
