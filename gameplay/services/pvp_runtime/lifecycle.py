from __future__ import annotations


def compute_symmetric_return_seconds(*, started_at, now) -> int:
    elapsed = max(0, int((now - started_at).total_seconds()))
    return max(1, elapsed)
