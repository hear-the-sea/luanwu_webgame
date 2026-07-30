from __future__ import annotations

import pytest
from django.utils import timezone

from core.config import BUILDING_KEYS
from gameplay.models import MissionRun, MissionTemplate
from gameplay.services.city_defense import apply_city_defense_battle_damage
from gameplay.services.missions_impl.execution_adapters import load_locked_mission_run, mark_run_completed
from gameplay.services.missions_impl.finalize_command import finalize_mission_run
from tests.battle_report_view.support import create_report


def _wall(manor):
    return manor.buildings.select_related("building_type").get(building_type__key=BUILDING_KEYS.WALL)


def _finalize(run: MissionRun, *, now, fail_after_damage: bool = False) -> None:
    def _apply_rewards(*_args, **_kwargs):
        if fail_after_damage:
            raise RuntimeError("injected mission settlement failure")

    finalize_mission_run(
        run,
        now=now,
        load_locked_mission_run=lambda run_pk: load_locked_mission_run(
            mission_run_model=MissionRun,
            run_pk=run_pk,
        ),
        build_defense_report_if_needed=lambda locked_run: locked_run.battle_report,
        extract_report_guest_state=lambda *_args, **_kwargs: ({}, set(), set()),
        select_guests_for_finalize=lambda *_args, **_kwargs: [],
        prepare_guest_updates_for_finalize=lambda *_args, **_kwargs: ([], []),
        mark_run_completed=mark_run_completed,
        apply_city_defense_battle_damage=apply_city_defense_battle_damage,
        apply_defender_troop_losses=lambda *_args, **_kwargs: None,
        return_attacker_troops_after_mission=lambda *_args, **_kwargs: None,
        apply_mission_rewards_if_won=_apply_rewards,
        send_mission_report_message=lambda *_args, **_kwargs: None,
    )


def _defense_run(manor, *, wall_hp: int = 2_000) -> MissionRun:
    report = create_report(
        manor=manor,
        opponent_name="来袭敌军",
        battle_type="task",
        defender_city_defenses=[
            {
                "schema_version": 2,
                "key": BUILDING_KEYS.WALL,
                "name": "城墙",
                "level": 1,
                "initial_hp": 3_000,
                "hp": wall_hp,
                "max_hp": 3_000,
                "recovered_before_battle": 0,
                "settled_hp": max(1, wall_hp),
                "destroyed": wall_hp <= 0,
            }
        ],
    )
    mission = MissionTemplate.objects.create(
        key=f"city_defense_settlement_{report.pk}",
        name="守城任务",
        is_defense=True,
    )
    return MissionRun.objects.create(
        manor=manor,
        mission=mission,
        battle_report=report,
        return_at=timezone.now(),
    )


@pytest.mark.django_db
def test_defense_mission_settles_city_defense_damage_once(manor_with_user):
    manor, _client = manor_with_user
    wall = _wall(manor)
    wall.current_hp = 3_000
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])
    run = _defense_run(manor)
    completed_at = timezone.now()

    _finalize(run, now=completed_at)
    _finalize(run, now=completed_at + timezone.timedelta(hours=1))

    wall.refresh_from_db()
    run.refresh_from_db()
    assert wall.current_hp == 2_000
    assert wall.hp_updated_at == completed_at
    assert run.status == MissionRun.Status.COMPLETED


@pytest.mark.django_db
def test_defense_mission_rolls_back_city_defense_when_later_settlement_fails(manor_with_user):
    manor, _client = manor_with_user
    wall = _wall(manor)
    original_updated_at = timezone.now()
    wall.current_hp = 3_000
    wall.hp_updated_at = original_updated_at
    wall.save(update_fields=["current_hp", "hp_updated_at"])
    run = _defense_run(manor, wall_hp=1)

    with pytest.raises(RuntimeError, match="injected mission settlement failure"):
        _finalize(run, now=timezone.now(), fail_after_damage=True)

    wall.refresh_from_db()
    run.refresh_from_db()
    assert wall.current_hp == 3_000
    assert wall.hp_updated_at == original_updated_at
    assert run.status == MissionRun.Status.ACTIVE
