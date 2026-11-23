from __future__ import annotations

from core.game_data.equipment_value import POWER_POINTS_PER_HP, combat_power_points, guest_power
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
    # Defense already adds 30 HP per point to max_hp.  Raising both defense and
    # max_hp by 30 must not be treated as two independent defense sources.
    raised_hp_only = _snapshot(max_hp=12030)
    raised_defense = _snapshot(defense=151, defense_stat=151, max_hp=12030)
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


def test_snapshot_power_v2_current_hp_only_scales_the_hp_component():
    snapshot = _snapshot(max_hp=12000, current_hp=12000)
    full = snapshot_power(snapshot, formula_version=2, hp=12000)
    half = snapshot_power(snapshot, formula_version=2, hp=6000)
    assert full - half == 600


def test_combat_power_points_matches_guest_power_at_full_health():
    max_hp = max(200, 1000 + 1500 + 150 * 40) + (40 - 1) * 70
    guest_kwargs = dict(
        force=200,
        intellect=80,
        defense=150,
        defense_stat=150,
        agility=100,
        luck=80,
        hp_bonus=1500,
        base_hp=1000,
        level=40,
        archetype="military",
    )
    assert combat_power_points(
        attack=182,
        defense=150,
        defense_stat=150,
        force=200,
        intellect=80,
        level=40,
        archetype="military",
        max_hp=max_hp,
        agility=100,
        luck=80,
    ) == round(guest_power(**guest_kwargs) / POWER_POINTS_PER_HP)


def test_defense_bonus_only_adds_mitigation_without_phantom_hp():
    from core.game_data.equipment_value import weights_for

    base_kwargs = dict(
        force=200,
        intellect=80,
        defense=150,
        defense_stat=150,
        agility=100,
        luck=80,
        hp_bonus=1500,
        base_hp=1000,
        level=45,
        archetype="military",
    )
    base = guest_power(**base_kwargs)
    with_def_bonus = guest_power(**{**base_kwargs, "defense": 160})
    with_def_stat = guest_power(**{**base_kwargs, "defense": 160, "defense_stat": 160})

    # Pure defense_bonus improves mitigation (EHP) without granting 30 HP/pt,
    # so defense_stat must strictly outperform defense_bonus.
    assert with_def_bonus > base
    assert with_def_stat > with_def_bonus
    for stage in ("early", "mid", "late"):
        w = weights_for(archetype="military", stage=stage)
        assert round(w.defense - w.defense_mitigation, 6) == 40.0


def test_effective_luck_is_concave_capped_at_300_and_zero_without_skills():
    from core.game_data.equipment_value import effective_luck

    gain_low = effective_luck(100) - effective_luck(50)
    gain_mid = effective_luck(180) - effective_luck(130)
    gain_high = effective_luck(280) - effective_luck(230)
    assert gain_low > gain_mid > gain_high > 0
    assert effective_luck(300) == effective_luck(450)
    assert effective_luck(200, active_skill_count=0) == 0.0


def test_civil_intellect_outweighs_force_and_model_is_strictly_monotonic_across_levels():
    base = dict(
        force=100,
        intellect=250,
        defense=140,
        defense_stat=140,
        agility=150,
        luck=80,
        hp_bonus=1200,
        base_hp=700,
        level=45,
        archetype="civil",
        troop_capacity=200,
        attack_bonus=10,
    )
    p0 = guest_power(**base)
    p_int = guest_power(**{**base, "intellect": 270})
    p_for = guest_power(**{**base, "force": 120})
    assert (p_int - p0) > (p_for - p0)

    # Strictly monotonic across former stage boundaries (lv15->16, lv55->56)
    prev = guest_power(**{**base, "level": 1})
    for lv in (14, 15, 16, 45, 55, 56, 80, 100):
        cur = guest_power(**{**base, "level": lv})
        assert cur > prev
        prev = cur


def test_snapshot_power_v2_evaluates_actual_skill_keys_and_zero_skill_luck():
    # Empty skill_keys explicitly means 0 equipped skills -> luck has no trigger benefit
    no_skill_low_luck = snapshot_power(_snapshot(luck=50, skill_keys=[]), formula_version=2)
    no_skill_high_luck = snapshot_power(_snapshot(luck=250, skill_keys=[]), formula_version=2)
    assert no_skill_low_luck == no_skill_high_luck

    # Equipping real active skills raises power substantially over 0 skills,
    # and stronger skills outperform weaker ones.
    weak_skill = snapshot_power(_snapshot(luck=120, skill_keys=["thunder_control"]), formula_version=2)
    strong_skill = snapshot_power(
        _snapshot(luck=120, skill_keys=["prison_break_blade", "city_felling_strike"]),
        formula_version=2,
    )
    assert strong_skill > weak_skill > no_skill_high_luck

    # Adding a passive skill grants an additional multiplier bonus
    with_passive = snapshot_power(
        _snapshot(luck=120, skill_keys=["prison_break_blade", "city_felling_strike", "iron_wall_heart"]),
        formula_version=2,
    )
    assert with_passive > strong_skill
