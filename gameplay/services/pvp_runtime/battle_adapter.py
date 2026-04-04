from __future__ import annotations

from typing import Any


def resolve_battle_winner(report: Any) -> str | None:
    winner = getattr(report, "winner", None)
    return str(winner) if winner is not None else None
