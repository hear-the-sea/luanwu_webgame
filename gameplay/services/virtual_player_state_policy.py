from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = [
    "VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES",
    "VIRTUAL_PROFILE_ATTACKABLE_STATES",
    "VIRTUAL_PROFILE_MAINTAINED_STATES",
    "VIRTUAL_PROFILE_MAP_VISIBLE_STATES",
    "VIRTUAL_PROFILE_REACTIVATABLE_STATES",
    "is_virtual_profile_arena_eligible",
    "is_virtual_profile_attackable",
    "is_virtual_profile_maintained",
    "is_virtual_profile_map_visible",
    "is_virtual_profile_reactivatable",
]


@dataclass(frozen=True, slots=True)
class _VirtualProfileCapabilities:
    map_visible: bool
    attackable: bool
    maintained: bool
    arena_eligible: bool
    reactivatable: bool


_NO_CAPABILITIES: Final = _VirtualProfileCapabilities(
    map_visible=False,
    attackable=False,
    maintained=False,
    arena_eligible=False,
    reactivatable=False,
)

ACTIVE_STATE: Final = "active"
SLOWING_STATE: Final = "slowing"
ABANDONED_STATE: Final = "abandoned"
RETIRED_STATE: Final = "retired"
STALE_STATE: Final = "stale"

_CAPABILITIES_BY_STATE: Final[Mapping[str, _VirtualProfileCapabilities]] = MappingProxyType(
    {
        ACTIVE_STATE: _VirtualProfileCapabilities(
            map_visible=True,
            attackable=True,
            maintained=True,
            arena_eligible=True,
            reactivatable=False,
        ),
        SLOWING_STATE: _VirtualProfileCapabilities(
            map_visible=True,
            attackable=True,
            maintained=True,
            arena_eligible=True,
            reactivatable=False,
        ),
        ABANDONED_STATE: _VirtualProfileCapabilities(
            map_visible=True,
            attackable=True,
            maintained=True,
            arena_eligible=False,
            reactivatable=True,
        ),
        RETIRED_STATE: _VirtualProfileCapabilities(
            map_visible=True,
            attackable=True,
            maintained=False,
            arena_eligible=False,
            reactivatable=True,
        ),
        STALE_STATE: _NO_CAPABILITIES,
    }
)

VIRTUAL_PROFILE_MAP_VISIBLE_STATES: Final[frozenset[str]] = frozenset(
    state for state, capabilities in _CAPABILITIES_BY_STATE.items() if capabilities.map_visible
)
VIRTUAL_PROFILE_ATTACKABLE_STATES: Final[frozenset[str]] = frozenset(
    state for state, capabilities in _CAPABILITIES_BY_STATE.items() if capabilities.attackable
)
VIRTUAL_PROFILE_MAINTAINED_STATES: Final[frozenset[str]] = frozenset(
    state for state, capabilities in _CAPABILITIES_BY_STATE.items() if capabilities.maintained
)
VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES: Final[frozenset[str]] = frozenset(
    state for state, capabilities in _CAPABILITIES_BY_STATE.items() if capabilities.arena_eligible
)
VIRTUAL_PROFILE_REACTIVATABLE_STATES: Final[frozenset[str]] = frozenset(
    state for state, capabilities in _CAPABILITIES_BY_STATE.items() if capabilities.reactivatable
)


def _state_value(profile_or_state: object) -> str | None:
    if isinstance(profile_or_state, str):
        return profile_or_state
    state = getattr(profile_or_state, "state", None)
    return state if isinstance(state, str) else None


def _capabilities(profile_or_state: object) -> _VirtualProfileCapabilities:
    state = _state_value(profile_or_state)
    if state is None:
        return _NO_CAPABILITIES
    return _CAPABILITIES_BY_STATE.get(state, _NO_CAPABILITIES)


def is_virtual_profile_map_visible(profile_or_state: object) -> bool:
    return _capabilities(profile_or_state).map_visible


def is_virtual_profile_attackable(profile_or_state: object) -> bool:
    return _capabilities(profile_or_state).attackable


def is_virtual_profile_maintained(profile_or_state: object) -> bool:
    return _capabilities(profile_or_state).maintained


def is_virtual_profile_arena_eligible(profile_or_state: object) -> bool:
    return _capabilities(profile_or_state).arena_eligible


def is_virtual_profile_reactivatable(profile_or_state: object) -> bool:
    return _capabilities(profile_or_state).reactivatable
