from __future__ import annotations

from gameplay.services.arena.power_settings import current_arena_power_formula_version
from gameplay.services.arena.virtual_lineups import (
    LineupSelectionContext,
    compare_power_formulas,
    evaluate_lineup_snapshots,
    lineup_power,
    snapshot_power,
)


def _snapshot(**overrides):
    payload = {
        "snapshot_version": 1,
        "guest_id": 1,
        "archetype": "military",
        "level": 40,
        "force": 200,
        "intellect": 80,
        "defense_stat": 150,
        "agility": 120,
        "luck": 75,
        "attack": 164,
        "defense": 150,
        "max_hp": 12000,
        "current_hp": 12000,
        "troop_capacity": 220,
    }
    payload.update(overrides)
    return payload


def test_snapshot_power_v1_is_unchanged_by_default():
    snapshot = _snapshot()
    assert snapshot_power(snapshot) == snapshot_power(snapshot, formula_version=1)
    assert lineup_power((snapshot,)) == snapshot_power(snapshot, formula_version=1)


def test_snapshot_power_v2_includes_luck_but_not_latent_troop_capacity():
    snapshot = _snapshot()
    base = snapshot_power(snapshot, formula_version=2)
    with_luck = snapshot_power(_snapshot(luck=125), formula_version=2)
    with_capacity = snapshot_power(_snapshot(troop_capacity=320), formula_version=2)
    assert with_luck > base
    # Arena lineups do not field troops; capacity is represented by troop_total
    # in the composite strength score, not by lineup power.
    assert with_capacity == base
    # The old proxy ignores luck entirely.
    assert snapshot_power(_snapshot(luck=125), formula_version=1) == snapshot_power(snapshot, formula_version=1)


def test_snapshot_power_v2_does_not_double_count_defense_hp():
    snapshot = _snapshot()
    # Defense already adds 50 HP per point to max_hp.  Raising both defense and
    # max_hp by 50 must not be treated as two independent defense sources.
    raised_hp_only = _snapshot(max_hp=12050)
    raised_defense = _snapshot(defense=151, defense_stat=151, max_hp=12050)
    assert snapshot_power(raised_hp_only, formula_version=2) > snapshot_power(snapshot, formula_version=2)
    assert snapshot_power(raised_defense, formula_version=2) > snapshot_power(raised_hp_only, formula_version=2)


def test_lineup_power_v2_sums_guest_powers():
    first = _snapshot(guest_id=1)
    second = _snapshot(guest_id=2, force=260, attack=200, max_hp=14000)
    assert lineup_power((first, second), formula_version=2) == snapshot_power(
        first, formula_version=2
    ) + snapshot_power(second, formula_version=2)


def test_evaluate_lineup_snapshots_uses_requested_formula_version():
    snapshots = tuple(_snapshot(guest_id=index) for index in range(1, 6))
    context = LineupSelectionContext(mode="tournament", event_id=1, profile_id=1, max_lineup_size=5)
    v1_target = lineup_power(snapshots, formula_version=1)
    v2_target = lineup_power(snapshots, formula_version=2)
    assert v2_target != v1_target

    v2_evaluation = evaluate_lineup_snapshots(
        snapshots,
        context=context,
        target_guest_count=5,
        target_team_power=v2_target,
        power_formula_version=2,
    )
    assert v2_evaluation.is_ready
    assert v2_evaluation.selected_power == v2_target

    # A v2 target is meaningless against the v1 formula and should not be
    # silently treated as a v1 match.
    v1_evaluation = evaluate_lineup_snapshots(
        snapshots,
        context=context,
        target_guest_count=5,
        target_team_power=v2_target,
        power_formula_version=1,
    )
    assert not v1_evaluation.is_ready


def test_current_formula_version_reads_settings(settings):
    settings.ARENA_POWER_FORMULA_VERSION = 2
    assert current_arena_power_formula_version() == 2
    settings.ARENA_POWER_FORMULA_VERSION = 1
    assert current_arena_power_formula_version() == 1


def test_compare_power_formulas_returns_both_versions():
    snapshot = _snapshot()
    result = compare_power_formulas((snapshot,))
    assert result == {
        "v1": snapshot_power(snapshot, formula_version=1),
        "v2": snapshot_power(snapshot, formula_version=2),
    }
