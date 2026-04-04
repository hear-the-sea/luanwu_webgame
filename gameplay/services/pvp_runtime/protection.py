from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockedTargetResult:
    blocked: bool
    reason: str
    return_time_seconds: int = 0


def build_daily_cap_result(*, current_count: int, max_count: int, blocked_reason: str) -> BlockedTargetResult:
    if int(current_count or 0) >= int(max_count or 0):
        return BlockedTargetResult(blocked=True, reason=blocked_reason)
    return BlockedTargetResult(blocked=False, reason="")
