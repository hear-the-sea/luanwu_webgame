from __future__ import annotations

import logging
from typing import Any


def audit_battle_replay_metadata(
    activity: Any,
    report: Any,
    *,
    logger: logging.Logger,
    activity_kind: str,
) -> bool:
    """Log persisted replay metadata mismatches without hiding the battle result."""

    expected = {
        "base_seed": int(getattr(activity, "base_seed", 0) or 0),
        "rng_version": int(getattr(activity, "rng_version", 0) or 0),
        "battle_engine_version": str(getattr(activity, "battle_engine_version", "") or ""),
    }
    actual = {
        "base_seed": int(getattr(report, "seed", 0) or 0),
        "rng_version": int(getattr(report, "rng_version", 0) or 0),
        "battle_engine_version": str(getattr(report, "battle_engine_version", "") or ""),
    }
    if expected == actual:
        return True

    activity_id = getattr(activity, "pk", None)
    report_id = getattr(report, "pk", None)
    logger.error(
        "battle_replay_mismatch: activity_kind=%s activity_id=%s report_id=%s expected=%s actual=%s",
        activity_kind,
        activity_id,
        report_id,
        expected,
        actual,
        extra={
            "event": "battle_replay_mismatch",
            "activity_kind": activity_kind,
            "activity_id": activity_id,
            "report_id": report_id,
            "expected_replay_metadata": expected,
            "actual_replay_metadata": actual,
        },
    )
    return False
