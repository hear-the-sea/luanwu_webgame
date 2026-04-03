from __future__ import annotations

import random
from types import SimpleNamespace

from battle.passives import run_passives_for_timing


def make_guest(template_key: str, *, base_hp: int) -> SimpleNamespace:
    template = SimpleNamespace(key=template_key, base_hp=base_hp)
    return SimpleNamespace(
        template=template,
        level=1,
        force=10,
        intellect=10,
        defense_stat=10,
        agility=10,
        luck=10,
        hp_bonus=0,
        current_hp=1,
        attack_bonus=0,
        defense_bonus=0,
    )


def make_unit(
    template_key: str,
    *,
    hp: int,
    max_hp: int,
    side: str = "defender",
    name: str | None = None,
    is_boss: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name or template_key,
        template_key=template_key,
        hp=hp,
        max_hp=max_hp,
        side=side,
        kind="guest",
        is_boss=is_boss,
        battle_modifiers={},
        battle_state={},
    )


def apply_round_start_passives(defender_units: list[SimpleNamespace]) -> None:
    for unit in defender_units:
        run_passives_for_timing(
            "round_start",
            actor=unit,
            target=None,
            attacker_team=[],
            defender_team=defender_units,
            round_no=1,
            event_sink=[],
            rng=random.Random(1),
        )
