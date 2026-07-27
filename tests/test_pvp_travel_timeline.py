from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from gameplay.services.pvp_runtime.lifecycle import TravelTimeline


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected_seconds"),
    [
        (-10, 0),
        (25, 25),
        (90, 60),
    ],
)
def test_travel_timeline_clamps_retreat_elapsed_to_outbound_window(elapsed_seconds, expected_seconds):
    now = timezone.now()
    started_at = now - timedelta(seconds=elapsed_seconds)
    activity = SimpleNamespace(
        started_at=started_at,
        complete_at=started_at + timedelta(seconds=60),
        travel_time=60,
        return_at=started_at + timedelta(seconds=120),
    )

    timeline = TravelTimeline.from_activity(activity)
    schedule = timeline.retreat_schedule(now=now)

    assert timeline.battle_at == activity.complete_at
    assert schedule.elapsed_seconds == expected_seconds
    assert schedule.return_at == now + timedelta(seconds=expected_seconds)
