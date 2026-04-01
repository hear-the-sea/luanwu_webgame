from __future__ import annotations

import pytest

from gameplay.services.manor.core import ensure_manor
from tests.battle_tasks_generate_report_task.support import assert_no_retry


@pytest.mark.django_db
def test_generate_report_task_offense_rejects_invalid_troop_loadout(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task

    user = django_user_model.objects.create_user(username="task_offense_bad_loadout", password="pass")
    manor = ensure_manor(user)

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission troop loadout"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=None,
            run_id=None,
            guest_ids=[],
            troop_loadout="bad-loadout",
            battle_type="skirmish",
        )


@pytest.mark.django_db
def test_generate_report_task_offense_rejects_invalid_defender_setup(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task

    user = django_user_model.objects.create_user(username="task_offense_bad_defender_setup", password="pass")
    manor = ensure_manor(user)

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission mapping payload"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=None,
            run_id=None,
            guest_ids=[],
            troop_loadout={},
            defender_setup="bad-defender-setup",
            battle_type="skirmish",
        )


@pytest.mark.django_db
def test_generate_report_task_rejects_invalid_guest_ids_payload(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task

    user = django_user_model.objects.create_user(username="task_bad_guest_ids", password="pass")
    manor = ensure_manor(user)

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission guest_ids"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=None,
            run_id=None,
            guest_ids="bad-guest-ids",
            troop_loadout={},
            battle_type="skirmish",
        )


@pytest.mark.django_db
def test_generate_report_task_rejects_invalid_travel_seconds(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task

    user = django_user_model.objects.create_user(username="task_bad_travel_seconds", password="pass")
    manor = ensure_manor(user)

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission travel_seconds"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=None,
            run_id=None,
            guest_ids=[],
            troop_loadout={},
            battle_type="skirmish",
            travel_seconds=-1,
        )


@pytest.mark.django_db
def test_generate_report_task_rejects_blank_battle_type(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task

    user = django_user_model.objects.create_user(username="task_blank_battle_type", password="pass")
    manor = ensure_manor(user)

    assert_no_retry(monkeypatch)

    with pytest.raises(AssertionError, match="invalid mission battle_type"):
        generate_report_task.run(
            manor_id=manor.id,
            mission_id=None,
            run_id=None,
            guest_ids=[],
            troop_loadout={},
            battle_type=" ",
        )


@pytest.mark.django_db
def test_generate_report_task_offense_uses_enemy_guest_count_as_defender_max_squad(monkeypatch, django_user_model):
    from battle.tasks import generate_report_task
    from gameplay.models import MissionTemplate

    user = django_user_model.objects.create_user(username="task_offense_enemy_guest_count", password="pass")
    manor = ensure_manor(user)
    mission = MissionTemplate.objects.create(
        key="task_offense_enemy_guest_count",
        name="敌方人数任务",
        battle_type="task",
        enemy_guests=[{"key": f"enemy_{i}"} for i in range(7)],
    )

    assert_no_retry(monkeypatch)

    captured = {}

    class _FakeReport:
        pk = 123

    def _fake_simulate_report(**kwargs):
        captured.update(kwargs)
        return _FakeReport()

    monkeypatch.setattr("battle.tasks.simulate_report", _fake_simulate_report)

    result = generate_report_task.run(
        manor_id=manor.id,
        mission_id=mission.id,
        run_id=None,
        guest_ids=[],
        troop_loadout={},
        battle_type="task",
        defender_setup={"guest_keys": mission.enemy_guests},
    )

    assert result == 123
    assert captured["max_squad"] == manor.max_squad_size
    assert captured["defender_max_squad"] == 7
