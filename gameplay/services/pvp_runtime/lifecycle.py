from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class RetreatSchedule:
    elapsed_seconds: int
    return_at: datetime


@dataclass(frozen=True)
class TravelTimeline:
    started_at: datetime
    battle_at: datetime | None
    travel_time: int
    return_at: datetime | None = None

    @classmethod
    def from_activity(cls, activity: Any) -> TravelTimeline:
        battle_at = getattr(activity, "battle_at", None)
        if battle_at is None:
            battle_at = getattr(activity, "complete_at", None)
        return cls(
            started_at=activity.started_at,
            battle_at=battle_at,
            travel_time=max(0, int(getattr(activity, "travel_time", 0) or 0)),
            return_at=getattr(activity, "return_at", None),
        )

    def elapsed_seconds(self, *, now: datetime) -> int:
        elapsed = max(0, int((now - self.started_at).total_seconds()))
        return min(elapsed, self.travel_time)

    def retreat_schedule(self, *, now: datetime) -> RetreatSchedule:
        elapsed = self.elapsed_seconds(now=now)
        return RetreatSchedule(
            elapsed_seconds=elapsed,
            return_at=now + timedelta(seconds=elapsed),
        )


def compute_symmetric_return_seconds(*, started_at, now, travel_time=None) -> int:
    resolved_travel_time = travel_time
    if resolved_travel_time is None:
        resolved_travel_time = max(0, int((now - started_at).total_seconds()))
    timeline = TravelTimeline(
        started_at=started_at,
        battle_at=None,
        travel_time=max(0, int(resolved_travel_time or 0)),
    )
    return timeline.elapsed_seconds(now=now)
