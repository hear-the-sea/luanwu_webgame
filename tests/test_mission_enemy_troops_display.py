from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.urls import reverse

from battle.models import TroopTemplate
from core.utils.yaml_schema import validate_mission_templates
from gameplay.models import MissionTemplate
from gameplay.services.missions_impl.launch_post_actions import build_defender_setup_and_drop_table
from gameplay.services.missions_impl.sync_report import _normalize_guest_configs, _normalize_mapping
from gameplay.views.mission_helpers import build_enemy_troop_display_rows, collect_mission_asset_keys


def test_display_override_is_separate_from_actual_battle_troops():
    mission = MissionTemplate(
        key="display_isolation",
        enemy_guests=["zhang_fei"],
        enemy_troops={"archer": 20},
        enemy_troops_display={"qiang_ke": 1000000},
    )

    rows = build_enemy_troop_display_rows(mission)
    _guests, troop_keys, _drops = collect_mission_asset_keys([mission])
    defender, drops = build_defender_setup_and_drop_table(
        mission,
        {},
        normalize_guest_configs=_normalize_guest_configs,
        normalize_mapping=_normalize_mapping,
    )

    assert rows == [{"key": "qiang_ke", "count_label": 1000000, "label": ""}]
    assert troop_keys == {"archer", "qiang_ke"}
    assert defender["troop_loadout"] == {"archer": 20}
    assert "enemy_troops_display" not in defender
    assert drops == {}
    assert mission.enemy_troops == {"archer": 20}


def test_ordinary_enemy_counts_keep_original_format():
    mission = MissionTemplate(enemy_troops={"archer": 1000000})

    assert build_enemy_troop_display_rows(mission) == [{"key": "archer", "count_label": 1000000, "label": ""}]
    assert build_enemy_troop_display_rows(None) == []


@pytest.mark.parametrize("display", [[], {"archer": -1}, {"archer": True}, {"archer": 1.5}, {"": 1}])
def test_invalid_display_configuration_is_rejected(display):
    mission = MissionTemplate(enemy_troops_display=display)

    with pytest.raises(AssertionError, match="invalid mission enemy_troops_display"):
        build_enemy_troop_display_rows(mission)
    result = validate_mission_templates(
        {"missions": [{"key": "display", "name": "兵力展示", "enemy_troops_display": display}]},
        troop_keys={"archer"},
    )
    assert not result.is_valid


def test_display_schema_checks_troop_references():
    result = validate_mission_templates(
        {
            "missions": [
                {
                    "key": "display",
                    "name": "兵力展示",
                    "enemy_troops_display": {"missing": 1000000},
                }
            ]
        },
        troop_keys={"archer"},
    )
    assert not result.is_valid
    assert "not found in troop_templates.yaml" in str(result.errors)


@pytest.mark.django_db
def test_loader_imports_and_clears_display_without_modifying_actual_troops(tmp_path):
    entry = {
        "key": "display_import",
        "name": "长坂桥",
        "enemy_troops": {"archer": 20},
        "enemy_troops_display": {"archer": 1000000},
    }
    path = tmp_path / "display.json"
    path.write_text(json.dumps({"missions": [entry]}), encoding="utf-8")
    call_command("load_mission_templates", file=str(path), verbosity=0)
    mission = MissionTemplate.objects.get(key=entry["key"])
    assert mission.enemy_troops_display == {"archer": 1000000}
    assert mission.enemy_troops == {"archer": 20}

    del entry["enemy_troops_display"]
    path.write_text(json.dumps({"missions": [entry]}), encoding="utf-8")
    call_command("load_mission_templates", file=str(path), verbosity=0)
    mission.refresh_from_db()
    assert mission.enemy_troops_display == {}
    assert mission.enemy_troops == {"archer": 20}


@pytest.mark.django_db
@pytest.mark.parametrize("avatar", ["", "troops/bridge-guards.png"])
def test_task_board_shows_million_guards_without_revealing_actual_count(manor_with_user, avatar):
    _manor, client = manor_with_user
    TroopTemplate.objects.update_or_create(key="bridge_display_guard", defaults={"name": "枪王", "avatar": avatar})
    mission = MissionTemplate.objects.create(
        key="bridge_display_mission",
        name="长坂桥张飞断水流",
        enemy_guests=[{"key": "zhang_fei", "label": "张飞"}],
        enemy_troops={"bridge_display_guard": 27},
        enemy_troops_display={"bridge_display_guard": 1000000},
    )

    response = client.get(reverse("gameplay:tasks"), {"mission": mission.key})

    assert response.status_code == 200
    html = response.content.decode()
    intel = html.split("敌人情报</h5>", 1)[1].split("</section>", 1)[0]
    assert "×1000000" in intel
    assert "100w" not in intel.lower()
    assert "×27" not in intel
    assert "枪王" in intel
    assert "疑兵" not in intel
    if avatar:
        assert "/media/troops/bridge-guards.png" in intel
        assert 'alt="枪王"' in intel
    else:
        assert "枪王 ×1000000" in intel
    mission.refresh_from_db()
    assert mission.enemy_troops == {"bridge_display_guard": 27}


@pytest.mark.django_db
def test_task_board_keeps_ordinary_troop_name_and_count(manor_with_user):
    _manor, client = manor_with_user
    TroopTemplate.objects.update_or_create(key="ordinary_display_guard", defaults={"name": "守桥枪兵"})
    mission = MissionTemplate.objects.create(
        key="ordinary_display_mission",
        name="普通任务",
        enemy_troops={"ordinary_display_guard": 1000000},
    )

    response = client.get(reverse("gameplay:tasks"), {"mission": mission.key})

    assert response.status_code == 200
    html = response.content.decode()
    intel = html.split("敌人情报</h5>", 1)[1].split("</section>", 1)[0]
    assert "守桥枪兵 ×1000000" in intel
    assert "100W" not in intel
