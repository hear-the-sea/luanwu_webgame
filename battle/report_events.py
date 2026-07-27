from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any


def _iter_nested_damage_events(events: Iterable[object]) -> Iterator[Mapping[str, Any]]:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        yield event
        additional_targets = event.get("additional_targets")
        if isinstance(additional_targets, list):
            yield from _iter_nested_damage_events(additional_targets)


def iter_damage_events(rounds: Iterable[object] | None) -> Iterator[Mapping[str, Any]]:
    """Yield primary and secondary target attack events from a battle report."""

    for battle_round in rounds or []:
        if not isinstance(battle_round, Mapping):
            continue
        events = battle_round.get("events")
        if isinstance(events, list):
            yield from _iter_nested_damage_events(events)
