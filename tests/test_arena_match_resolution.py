from __future__ import annotations

import logging
import random
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

import gameplay.services.arena.match_helpers as arena_match_helpers
from battle.models import BattleReport
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaMatch, ArenaTournament
from gameplay.services.arena.core import run_due_arena_rounds
from gameplay.services.arena.match_helpers import create_scheduled_match, resolve_match_locked, resolve_report_winner
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User, create_guest, create_guest_template, snapshot_from_guest


def _create_entry(
    tournament: ArenaTournament,
    *,
    label: str,
    invalid_snapshot: bool = False,
) -> ArenaEntry:
    user = User.objects.create_user(username=label, password="pass123")
    manor = ensure_manor(user)
    template = create_guest_template(f"{label}_tpl")
    guest = create_guest(manor, template, "A")
    entry = ArenaEntry.objects.create(tournament=tournament, manor=manor)
    ArenaEntryGuest.objects.create(
        entry=entry,
        guest=guest,
        snapshot={"display_name": "损坏快照"} if invalid_snapshot else snapshot_from_guest(guest),
    )
    return entry


def _create_running_tournament(*, label: str, now, player_limit: int = 2) -> ArenaTournament:
    return ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=player_limit,
        current_round=1,
        round_interval_seconds=600,
        started_at=now - timedelta(minutes=1),
        next_round_at=now - timedelta(seconds=1),
        base_seed=20260726,
        rng_version=1,
        battle_engine_version="2",
    )


@pytest.mark.django_db
def test_invalid_snapshot_forfeit_does_not_block_other_match_in_round(monkeypatch):
    now = timezone.now()
    tournament = _create_running_tournament(label="arena_invalid_round", now=now, player_limit=4)
    invalid_attacker = _create_entry(
        tournament,
        label="arena_invalid_round_bad",
        invalid_snapshot=True,
    )
    valid_defender = _create_entry(tournament, label="arena_invalid_round_defender")
    valid_attacker = _create_entry(tournament, label="arena_invalid_round_attacker")
    second_defender = _create_entry(tournament, label="arena_invalid_round_second_defender")
    invalid_match = create_scheduled_match(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=invalid_attacker,
        defender_entry=valid_defender,
    )
    valid_match = create_scheduled_match(
        tournament=tournament,
        round_number=1,
        match_index=1,
        attacker_entry=valid_attacker,
        defender_entry=second_defender,
    )

    def _simulate_report(**kwargs):
        return BattleReport.objects.create(
            manor=kwargs["manor"],
            opponent_name=kwargs["opponent_name"],
            battle_type="arena",
            attacker_team=[{"initial_hp": 100, "remaining_hp": 75}],
            defender_team=[{"initial_hp": 100, "remaining_hp": 25}],
            rounds=[],
            losses={"attacker": {}, "defender": {}},
            drops={},
            winner="draw",
            seed=kwargs["seed"],
            rng_version=kwargs["rng_version"],
            battle_engine_version=kwargs["battle_engine_version"],
            starts_at=now,
            completed_at=now,
        )

    monkeypatch.setattr(arena_match_helpers, "simulate_report", _simulate_report)

    assert run_due_arena_rounds(now=now, limit=10) == 1

    invalid_match.refresh_from_db()
    valid_match.refresh_from_db()
    assert invalid_match.base_seed > 0
    assert invalid_match.rng_version > 0
    assert invalid_match.battle_engine_version != "legacy"
    assert invalid_match.status == ArenaMatch.Status.FORFEIT
    assert invalid_match.winner_entry_id == valid_defender.pk
    assert "攻击方报名快照无效" in invalid_match.notes
    assert valid_match.status == ArenaMatch.Status.COMPLETED
    assert valid_match.winner_entry_id == valid_attacker.pk
    assert valid_match.battle_report_id is not None
    assert "剩余有效HP比例" in valid_match.notes


@pytest.mark.django_db
def test_double_invalid_snapshot_forfeit_replays_same_side_from_match_seed():
    outcomes: list[tuple[bool, int, str]] = []
    now = timezone.now()
    for index in range(2):
        tournament = _create_running_tournament(label=f"arena_double_invalid_{index}", now=now)
        attacker = _create_entry(
            tournament,
            label=f"arena_double_invalid_attacker_{index}",
            invalid_snapshot=True,
        )
        defender = _create_entry(
            tournament,
            label=f"arena_double_invalid_defender_{index}",
            invalid_snapshot=True,
        )
        match = create_scheduled_match(
            tournament=tournament,
            round_number=1,
            match_index=0,
            attacker_entry=attacker,
            defender_entry=defender,
        )

        winner = resolve_match_locked(
            tournament=tournament,
            round_number=1,
            match_index=0,
            attacker_entry=attacker,
            defender_entry=defender,
            now=now,
            max_guests_per_entry=10,
            arena_match_resolution_error=RuntimeError,
            match=match,
            logger=logging.getLogger("tests.arena.double_invalid"),
        )

        match.refresh_from_db()
        outcomes.append((winner.pk == attacker.pk, match.base_seed, match.notes))

    assert outcomes[0] == outcomes[1]
    assert "双方报名快照均无效" in outcomes[0][2]
    assert "tie_break" in outcomes[0][2]


def test_tied_report_uses_four_stage_resolution_order():
    attacker = SimpleNamespace(pk=1)
    defender = SimpleNamespace(pk=2)
    equal_team = [{"initial_hp": 100, "remaining_hp": 50}]

    hp_report = SimpleNamespace(
        winner="draw",
        attacker_team=[{"initial_hp": 100, "remaining_hp": 60}],
        defender_team=equal_team,
        rounds=[],
    )
    damage_report = SimpleNamespace(
        winner="draw",
        attacker_team=equal_team,
        defender_team=equal_team,
        rounds=[
            {
                "events": [
                    {"side": "attacker", "applied_damage": 30, "kills": 0},
                    {"side": "defender", "applied_damage": 20, "kills": 0},
                ]
            }
        ],
    )
    units_report = SimpleNamespace(
        winner="draw",
        attacker_team=equal_team,
        defender_team=equal_team,
        rounds=[
            {
                "events": [
                    {"side": "attacker", "applied_damage": 30, "kills": 1},
                    {"side": "defender", "applied_damage": 30, "kills": 0},
                ]
            }
        ],
    )
    tie_break_report = SimpleNamespace(
        winner="draw",
        attacker_team=equal_team,
        defender_team=equal_team,
        rounds=[],
    )

    winner, note = resolve_report_winner(
        hp_report,
        attacker_entry=attacker,
        defender_entry=defender,
        rng=random.Random(1),
    )
    assert winner is attacker
    assert "剩余有效HP比例" in note

    winner, note = resolve_report_winner(
        damage_report,
        attacker_entry=attacker,
        defender_entry=defender,
        rng=random.Random(1),
    )
    assert winner is attacker
    assert "有效伤害 30 : 20" in note

    winner, note = resolve_report_winner(
        units_report,
        attacker_entry=attacker,
        defender_entry=defender,
        rng=random.Random(1),
    )
    assert winner is attacker
    assert "击杀/剩余单位 1/1 : 0/1" in note

    expected = random.Random(7).choice([attacker, defender])
    winner, note = resolve_report_winner(
        tie_break_report,
        attacker_entry=attacker,
        defender_entry=defender,
        rng=random.Random(7),
    )
    assert winner is expected
    assert "tie_break" in note
