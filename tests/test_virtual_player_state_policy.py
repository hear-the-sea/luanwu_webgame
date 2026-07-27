from __future__ import annotations

from types import SimpleNamespace

import pytest

from gameplay.models import BotProfile
from gameplay.services.virtual_player_state_policy import (
    VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
    VIRTUAL_PROFILE_ATTACKABLE_STATES,
    VIRTUAL_PROFILE_MAINTAINED_STATES,
    VIRTUAL_PROFILE_MAP_VISIBLE_STATES,
    VIRTUAL_PROFILE_REACTIVATABLE_STATES,
    is_virtual_profile_arena_eligible,
    is_virtual_profile_attackable,
    is_virtual_profile_maintained,
    is_virtual_profile_map_visible,
    is_virtual_profile_reactivatable,
)

CAPABILITY_MATRIX = (
    (BotProfile.State.ACTIVE, True, True, True, True, False),
    (BotProfile.State.SLOWING, True, True, True, True, False),
    (BotProfile.State.ABANDONED, True, True, True, False, True),
    (BotProfile.State.RETIRED, True, True, False, False, True),
    (BotProfile.State.STALE, False, False, False, False, False),
)


@pytest.mark.parametrize(
    ("state", "map_visible", "attackable", "maintained", "arena_eligible", "reactivatable"),
    CAPABILITY_MATRIX,
    ids=["active", "slowing", "abandoned", "retired", "stale"],
)
def test_virtual_profile_state_capability_matrix(
    state: str,
    map_visible: bool,
    attackable: bool,
    maintained: bool,
    arena_eligible: bool,
    reactivatable: bool,
) -> None:
    for subject in (state, SimpleNamespace(state=state)):
        assert is_virtual_profile_map_visible(subject) is map_visible
        assert is_virtual_profile_attackable(subject) is attackable
        assert is_virtual_profile_maintained(subject) is maintained
        assert is_virtual_profile_arena_eligible(subject) is arena_eligible
        assert is_virtual_profile_reactivatable(subject) is reactivatable


def test_virtual_profile_state_sets_match_capability_matrix() -> None:
    assert {state for state, *_capabilities in CAPABILITY_MATRIX} == set(BotProfile.State.values)
    assert VIRTUAL_PROFILE_MAP_VISIBLE_STATES == frozenset(
        state for state, map_visible, *_rest in CAPABILITY_MATRIX if map_visible
    )
    assert VIRTUAL_PROFILE_ATTACKABLE_STATES == frozenset(
        state for state, _map_visible, attackable, *_rest in CAPABILITY_MATRIX if attackable
    )
    assert VIRTUAL_PROFILE_MAINTAINED_STATES == frozenset(
        state for state, _map_visible, _attackable, maintained, *_rest in CAPABILITY_MATRIX if maintained
    )
    assert VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES == frozenset(
        state
        for state, _map_visible, _attackable, _maintained, arena_eligible, _reactivatable in CAPABILITY_MATRIX
        if arena_eligible
    )
    assert VIRTUAL_PROFILE_REACTIVATABLE_STATES == frozenset(
        state
        for state, _map_visible, _attackable, _maintained, _arena_eligible, reactivatable in CAPABILITY_MATRIX
        if reactivatable
    )


@pytest.mark.parametrize(
    "subject",
    ["unknown", SimpleNamespace(state="unknown"), SimpleNamespace(state=None), object()],
)
def test_unknown_virtual_profile_state_has_no_capabilities(subject: object) -> None:
    assert is_virtual_profile_map_visible(subject) is False
    assert is_virtual_profile_attackable(subject) is False
    assert is_virtual_profile_maintained(subject) is False
    assert is_virtual_profile_arena_eligible(subject) is False
    assert is_virtual_profile_reactivatable(subject) is False


def test_retired_state_keeps_database_value_but_displays_as_sleeping() -> None:
    assert BotProfile.State.RETIRED.value == "retired"
    assert BotProfile.State.RETIRED.label == "休眠"
