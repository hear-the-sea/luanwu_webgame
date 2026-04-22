from __future__ import annotations

import pytest

from gameplay.models import PlayerTechnology
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestTemplate
from tests.battle_tasks_generate_report_task.support import assert_no_retry


@pytest.mark.django_db
def test_generate_report_task_defense_rejects_invalid_enemy_technology(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task
    from gameplay.models import MissionTemplate

    user = django_user_model.objects.create_user(username="task_defense_bad_tech", password="pass")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="m_task_defense_bad_tech",
        name="DefenseTask",
        is_defense=True,
        enemy_technology="bad-config",
        enemy_troops="bad-troops",
        enemy_guests="bad-guests",
    )
    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission enemy technology"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=mission.id,
            run_id=None,
            guest_ids=[],
            troop_loadout={},
            battle_type="task",
        )


@pytest.mark.django_db
def test_generate_report_task_defense_rejects_invalid_enemy_guest_mapping_skills(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task
    from gameplay.models import MissionTemplate

    user = django_user_model.objects.create_user(username="task_defense_bad_guest_skills", password="pass")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="m_task_defense_bad_guest_skills",
        name="DefenseTaskGuestSkills",
        is_defense=True,
        enemy_technology={},
        enemy_troops={},
        enemy_guests=[{"key": "enemy_guest", "skills": "bad-skills"}],
    )

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission guest config skills"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=mission.id,
            run_id=None,
            guest_ids=[],
            troop_loadout={},
            battle_type="task",
        )


@pytest.mark.django_db
def test_generate_report_task_defense_passes_enemy_label_override_to_ai_guests(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task
    from gameplay.models import MissionTemplate

    captured: dict[str, object] = {}

    def _fake_simulate_report(**kwargs):
        captured.update(kwargs)
        return type("DummyReport", (), {"pk": 1})()

    user = django_user_model.objects.create_user(username="task_defense_guest_label", password="pass")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="m_task_defense_guest_label",
        name="DefenseTaskGuestLabel",
        is_defense=True,
        enemy_technology={"guest_level": 30},
        enemy_troops={},
        enemy_guests=[{"key": "enemy_guest", "label": "任务别名"}],
    )
    template = GuestTemplate.objects.create(
        key="enemy_guest",
        name="模板原名",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=90,
        base_defense=80,
        base_agility=70,
        base_luck=60,
        base_hp=1200,
    )

    assert template.key == "enemy_guest"
    assert_no_retry(monkeypatch)
    monkeypatch.setattr("battle.tasks.simulate_report", _fake_simulate_report)
    monkeypatch.setattr("battle.combatants_pkg.ai_generator.get_all_guest_templates", lambda: {"enemy_guest": template})

    report_id = generate_report_task.run(
        manor_id=manor.id,
        mission_id=mission.id,
        run_id=None,
        guest_ids=[],
        troop_loadout={},
        battle_type="task",
    )

    attacker_guests = captured["attacker_guests"]
    assert report_id == 1
    assert len(attacker_guests) == 1
    assert getattr(attacker_guests[0], "_display_name_override", None) == "任务别名"


@pytest.mark.django_db
def test_generate_report_task_defense_rejects_invalid_defender_troop_loadout(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task
    from gameplay.models import MissionTemplate

    user = django_user_model.objects.create_user(username="task_defense_bad_defender_loadout", password="pass")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="m_task_defense_bad_defender_loadout",
        name="DefenseTaskDefenderLoadout",
        is_defense=True,
        enemy_technology={},
        enemy_troops={},
        enemy_guests=[],
    )

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission troop loadout quantity"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=mission.id,
            run_id=None,
            guest_ids=[],
            troop_loadout={"archer": "bad"},
            battle_type="task",
        )


@pytest.mark.django_db
def test_generate_report_task_defense_passes_player_technology_to_defender_setup(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task
    from gameplay.models import MissionTemplate

    captured: dict[str, object] = {}

    def _fake_simulate_report(**kwargs):
        captured.update(kwargs)
        return type("DummyReport", (), {"pk": 1})()

    user = django_user_model.objects.create_user(username="task_defense_player_tech", password="pass")
    manor = ensure_manor(user)
    PlayerTechnology.objects.create(manor=manor, tech_key="gong_attack", level=7)
    PlayerTechnology.objects.create(manor=manor, tech_key="gong_hp", level=5)
    mission = MissionTemplate.objects.create(
        key="m_task_defense_player_tech",
        name="DefenseTaskPlayerTech",
        is_defense=True,
        enemy_technology={},
        enemy_troops={},
        enemy_guests=[],
    )

    assert_no_retry(monkeypatch)
    monkeypatch.setattr("battle.tasks.simulate_report", _fake_simulate_report)

    report_id = generate_report_task.run(
        manor_id=manor.id,
        mission_id=mission.id,
        run_id=None,
        guest_ids=[],
        troop_loadout={"archer": 12},
        battle_type="task",
    )

    assert report_id == 1
    assert captured["defender_setup"] == {
        "troop_loadout": {"archer": 12},
        "technology": {"levels": {"gong_attack": 7, "gong_hp": 5}},
    }
