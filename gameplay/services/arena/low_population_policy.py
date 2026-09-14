"""Opt-in timing and bounded scheduling for low-population arenas."""

from datetime import datetime, timedelta

from django.conf import settings


def low_population_enabled() -> bool:
    return bool(getattr(settings, "ARENA_LOW_POPULATION_MODE", False))


def new_event_wait_seconds(kind: str, normal_wait_seconds: int) -> int:
    if kind not in {"tournament", "coop"}:
        raise ValueError(f"Unknown arena kind: {kind}")
    if not low_population_enabled():
        return normal_wait_seconds
    setting = "ARENA_LOW_POPULATION_COOP_WAIT_SECONDS" if kind == "coop" else "ARENA_LOW_POPULATION_WAIT_SECONDS"
    return max(1, int(getattr(settings, setting, 1800 if kind == "coop" else 900)))


def growth_interval(*, fill_at: datetime | None, now: datetime, normal: timedelta) -> timedelta:
    if not low_population_enabled() or fill_at is None:
        return normal
    boundary = fill_at - timedelta(minutes=15)
    if now >= boundary:
        return min(normal, timedelta(minutes=5))
    return min(normal, boundary - now)


def growth_batch_limits(limit: int, per_demand: int) -> tuple[int, int]:
    normalized_limit = max(0, int(limit))
    normalized_per_demand = max(0, int(per_demand))
    if low_population_enabled():
        return min(normalized_limit, 8), min(normalized_per_demand, 2)
    return normalized_limit, normalized_per_demand
