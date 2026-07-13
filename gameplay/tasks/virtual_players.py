from __future__ import annotations

import logging

from celery import shared_task

from gameplay.services.virtual_players import (
    maintain_due_virtual_players,
    plan_virtual_player_population,
    roll_virtual_player_population,
)

logger = logging.getLogger(__name__)


@shared_task(name="gameplay.plan_virtual_players")
def plan_virtual_players_task() -> dict:
    """Record an instantaneous virtual-player population plan without creating players."""
    return plan_virtual_player_population()


@shared_task(name="gameplay.roll_virtual_players")
def roll_virtual_players_task(limit: int | None = None) -> int:
    """Apply a small rolling slice of virtual-player population changes."""
    maintain_due_virtual_players(limit=100)
    created = roll_virtual_player_population(limit=limit)
    logger.info("Rolled virtual player population: created=%d", created)
    return created
