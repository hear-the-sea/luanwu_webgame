"""Asset-use policy for V2 virtual players.

The normal game catalog contains buildings and technologies used by human
players for activities that V2 virtual players never execute.  Keeping those
catalog entries available is useful for the real game, but they must not leak
into virtual-player planning or bootstrap progression.
"""

from __future__ import annotations

from collections.abc import Iterable

from common.constants.virtual_players import VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS
from gameplay.services.technology_catalog import build_technology_index

VIRTUAL_PLAYER_RETAINER_COUNT = 0

# These are the only buildings currently consumed by a V2 virtual player's
# resource, roster, recruitment, or maintenance paths.  The order is also the
# stable fallback order used when a legacy focus list becomes empty after
# filtering.
VIRTUAL_PLAYER_USEFUL_BUILDING_KEYS = (
    "farm",
    "tax_office",
    "bathhouse",
    "latrine",
    "granary",
    "silver_vault",
    "juxianzhuang",
    "tavern",
    "citang",
)
_VIRTUAL_PLAYER_USEFUL_BUILDINGS = frozenset(VIRTUAL_PLAYER_USEFUL_BUILDING_KEYS)


def useful_virtual_technology_keys(troop_classes: Iterable[str]) -> frozenset[str]:
    """Return technologies that can affect a virtual player's current plan.

    Basic economy technologies are always useful.  Martial technologies are
    selected from the catalog by troop class, so adding a new technology to a
    class automatically makes it eligible without maintaining another list.
    Technologies for classes absent from the player's troop mix remain out of
    the virtual policy.
    """

    active_classes = frozenset(
        str(value).strip()
        for value in troop_classes
        if str(value).strip() and str(value).strip() not in VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS
    )
    useful = {"architecture", "farming"}
    for key, template in build_technology_index().items():
        if str(template.get("troop_class") or "").strip() in active_classes:
            useful.add(str(key))
    return frozenset(useful)


def filter_virtual_building_focuses(focuses: Iterable[str]) -> tuple[str, ...]:
    """Keep configured building focuses that have a V2 runtime consumer."""

    return tuple(
        dict.fromkeys(str(key).strip() for key in focuses if str(key).strip() in _VIRTUAL_PLAYER_USEFUL_BUILDINGS)
    )


def is_virtual_player_building_useful(building_key: str) -> bool:
    """Return whether a building has a current V2 runtime consumer."""

    return str(building_key).strip() in _VIRTUAL_PLAYER_USEFUL_BUILDINGS


def resolve_virtual_technology_focuses(
    focuses: Iterable[str],
    *,
    troop_classes: Iterable[str],
) -> tuple[str, ...]:
    """Return useful technology focuses in stable priority order.

    Preserve configured priorities while removing technologies outside the
    active troop classes.  Basic economy technologies are appended as a
    stable fallback; we intentionally do not expand every cycle with every
    active-class technology, because the development plan still owns action
    pressure and cadence.
    """

    useful = useful_virtual_technology_keys(troop_classes)
    resolved = list(dict.fromkeys(str(key).strip() for key in focuses if str(key).strip() in useful))
    for key in ("architecture", "farming"):
        if key in useful and key not in resolved:
            resolved.append(key)
    return tuple(resolved)


__all__ = [
    "VIRTUAL_PLAYER_RETAINER_COUNT",
    "VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS",
    "VIRTUAL_PLAYER_USEFUL_BUILDING_KEYS",
    "filter_virtual_building_focuses",
    "is_virtual_player_building_useful",
    "resolve_virtual_technology_focuses",
    "useful_virtual_technology_keys",
]
