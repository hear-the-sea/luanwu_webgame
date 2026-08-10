from __future__ import annotations

from gameplay.services.virtual_player_core.contracts import ArenaGrowthObjective
from gameplay.services.virtual_player_core.maintenance_arena_projection import project_arena_candidate_selected_power


def _objective(*, critical_guest_count: int = 2) -> ArenaGrowthObjective:
    return ArenaGrowthObjective(
        critical_guest_count=critical_guest_count,
        preferred_guest_count=critical_guest_count,
        selected_power_lower_bound=80,
        selected_power_upper_bound=120,
        selected_power_before=90,
        target_team_power=100,
        lineup_mode="tournament",
        lineup_event_id=7,
        lineup_max_size=5,
        minimum_guest_level=1,
        recruitment_rarity_cap="gray",
        max_guest_level_step=6,
    )


def test_projection_preserves_the_over_cap_power_when_every_legal_lineup_is_too_strong() -> None:
    projection = project_arena_candidate_selected_power(
        objective=_objective(),
        profile_id=11,
        eligible_guest_powers_before=((1, 40), (2, 50)),
        existing_guest_power_after=(2, 90),
    )

    assert projection.selected_power_before == 90
    assert projection.projected_selected_power == 130
    assert projection.has_legal_lineup_after is True


def test_projection_reselects_the_lineup_after_recruitment() -> None:
    projection = project_arena_candidate_selected_power(
        objective=_objective(),
        profile_id=11,
        eligible_guest_powers_before=((1, 40),),
        added_guest_powers=(90,),
        added_guest_id_start=2,
    )

    assert projection.has_legal_lineup_after is True
    assert projection.projected_selected_power == 130


def test_projection_does_not_apply_the_event_cap_before_a_legal_size_exists() -> None:
    projection = project_arena_candidate_selected_power(
        objective=_objective(critical_guest_count=3),
        profile_id=11,
        eligible_guest_powers_before=((1, 90),),
        added_guest_powers=(90,),
        added_guest_id_start=2,
    )

    assert projection.projected_selected_power == 180
    assert projection.has_legal_lineup_after is False


def test_projection_adds_a_cured_injured_guest_to_eligibility() -> None:
    projection = project_arena_candidate_selected_power(
        objective=_objective(),
        profile_id=11,
        eligible_guest_powers_before=((1, 40),),
        newly_eligible_guest_power=(2, 90),
    )

    assert projection.has_legal_lineup_after is True
    assert projection.projected_selected_power == 130
