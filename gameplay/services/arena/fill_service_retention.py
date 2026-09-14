"""Bounded retention for the durable arena fill-service evidence tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction

from gameplay.models import ArenaFillServiceEpisode, ArenaFillServiceOutcome

TERMINAL_SLO_RESULTS = (
    "virtual_filled",
    "human_satisfied",
    "failed",
    "unknown",
    "cancelled",
)


@dataclass(frozen=True, slots=True)
class FillServiceCleanupResult:
    """Counts for one bounded, dependency-safe cleanup batch."""

    episodes: int = 0
    outcomes: int = 0

    @property
    def deleted(self) -> int:
        return self.episodes + self.outcomes


def eligible_fill_service_episodes(*, cutoff: datetime):
    """Return publish-complete episodes only after every outcome has aged out.

    Publication may be delayed arbitrarily. Keep the whole evidence aggregate
    while any newly published outcome is still within its retention window.
    """

    return ArenaFillServiceEpisode.objects.filter(
        closed_at__isnull=False,
        last_observed_at__lt=cutoff,
        slo_result__in=TERMINAL_SLO_RESULTS,
        outcomes__phase="slo",
        publication_pending=False,
        pending_outcomes={},
    ).exclude(outcomes__published_at__gte=cutoff)


def fill_service_outcomes_for_retention(*, cutoff: datetime):
    """Return outcomes belonging to episodes safe to remove at ``cutoff``."""

    return ArenaFillServiceOutcome.objects.filter(episode__in=eligible_fill_service_episodes(cutoff=cutoff))


@transaction.atomic
def cleanup_fill_service_rows(
    *,
    cutoff: datetime,
    batch_size: int,
    dry_run: bool = False,
) -> FillServiceCleanupResult:
    """Delete one dependency-safe batch, preserving open or unpublished evidence.

    Episodes are locked before deletion so observation and publication cannot
    add a protected outcome between the outcome and episode deletes. Outcomes
    are deleted first because their foreign key intentionally uses PROTECT.
    """

    normalized_batch_size = max(1, int(batch_size))
    candidates = eligible_fill_service_episodes(cutoff=cutoff).order_by("last_observed_at", "pk")
    if not dry_run:
        candidates = candidates.select_for_update()
    episodes = list(candidates[:normalized_batch_size])
    if not episodes:
        return FillServiceCleanupResult()

    episode_ids = [int(episode.pk) for episode in episodes]
    outcomes = ArenaFillServiceOutcome.objects.filter(episode_id__in=episode_ids)
    outcome_count = int(outcomes.count())
    episode_count = len(episode_ids)
    if dry_run:
        return FillServiceCleanupResult(episodes=episode_count, outcomes=outcome_count)

    deleted_outcomes, _ = outcomes.delete()
    deleted_episodes, _ = ArenaFillServiceEpisode.objects.filter(pk__in=episode_ids).delete()
    return FillServiceCleanupResult(episodes=int(deleted_episodes), outcomes=int(deleted_outcomes))


__all__ = [
    "FillServiceCleanupResult",
    "cleanup_fill_service_rows",
    "eligible_fill_service_episodes",
    "fill_service_outcomes_for_retention",
]
