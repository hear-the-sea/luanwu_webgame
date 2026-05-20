from __future__ import annotations

from typing import Any

from django.utils import timezone

from battle.models import BattleReport


def create_report(
    *,
    manor: Any,
    opponent_name: str,
    battle_type: str,
    attacker_team: list[dict[str, Any]] | None = None,
    defender_team: list[dict[str, Any]] | None = None,
    attacker_city_defenses: list[dict[str, Any]] | None = None,
    defender_city_defenses: list[dict[str, Any]] | None = None,
    rounds: list[dict[str, Any]] | None = None,
    winner: str = "attacker",
    seed: int = 1,
) -> BattleReport:
    now = timezone.now()
    return BattleReport.objects.create(
        manor=manor,
        opponent_name=opponent_name,
        battle_type=battle_type,
        attacker_team=attacker_team or [{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        attacker_city_defenses=attacker_city_defenses or [],
        defender_team=defender_team or [{"name": "D", "guest_id": 2, "template_key": "d"}],
        defender_troops={},
        defender_city_defenses=defender_city_defenses or [],
        rounds=rounds or [],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner=winner,
        starts_at=now,
        completed_at=now,
        seed=seed,
    )
