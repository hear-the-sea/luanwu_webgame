from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from gameplay.models import BotProfile, Manor, RaidRun
from gameplay.services.pvp_runtime.loot import normalize_positive_int_mapping
from gameplay.services.virtual_players import load_virtual_player_config, retire_virtual_player_if_unprotected


def _resource_total(resources: dict[str, int]) -> int:
    return sum(normalize_positive_int_mapping(resources).values())


def _clamp_resources_to_budget(resources: dict[str, int], budget: int) -> dict[str, int]:
    remaining = max(0, int(budget or 0))
    if remaining <= 0:
        return {}

    clamped: dict[str, int] = {}
    for key, amount in normalize_positive_int_mapping(resources).items():
        if remaining <= 0:
            break
        take = min(amount, remaining)
        if take > 0:
            clamped[key] = take
            remaining -= take
    return clamped


def _real_attacker_daily_resource_cap() -> int:
    config = load_virtual_player_config()
    projection = config.get("projection") or {}
    loot_limits = projection.get("loot_limits") or config.get("loot_limits") or {}
    return max(0, int(loot_limits.get("real_attacker_daily_resource_cap", 2_000_000) or 0))


def _spent_from_bots_today(attacker: Manor, *, now) -> int:
    since = now - timedelta(hours=24)
    runs = RaidRun.objects.filter(
        attacker=attacker,
        defender__bot_profile__isnull=False,
        is_attacker_victory=True,
        started_at__gte=since,
    ).only("loot_resources")
    return sum(_resource_total(run.loot_resources) for run in runs)


def _spent_from_bot_defender_today(defender: Manor, *, now) -> int:
    since = now - timedelta(hours=24)
    runs = RaidRun.objects.filter(
        defender=defender,
        is_attacker_victory=True,
        started_at__gte=since,
    ).only("loot_resources")
    return sum(_resource_total(run.loot_resources) for run in runs)


def _is_bot_manor(manor: Manor) -> bool:
    return BotProfile.objects.filter(manor=manor).exists()


def clamp_bot_loot_resources(
    *,
    attacker: Manor,
    defender: Manor,
    loot_resources: dict[str, int],
    now: Any = None,
) -> dict[str, int]:
    """Apply Bot defender and real-player-from-Bot daily resource caps."""
    now = now or timezone.now()
    normalized = normalize_positive_int_mapping(loot_resources)
    if not normalized:
        return {}

    profile = BotProfile.objects.filter(manor=defender).first()
    if profile is None:
        return normalized

    bot_budget = max(0, int(profile.loot_budget_daily or 0))
    remaining_bot_budget = max(0, bot_budget - _spent_from_bot_defender_today(defender, now=now))
    clamped = _clamp_resources_to_budget(normalized, remaining_bot_budget)
    if (
        remaining_bot_budget <= 0
        and bot_budget > 0
        and profile.state
        not in {
            BotProfile.State.STALE,
            BotProfile.State.RETIRED,
        }
    ):
        retire_virtual_player_if_unprotected(profile.pk, now=now)

    if not _is_bot_manor(attacker):
        real_cap = _real_attacker_daily_resource_cap()
        if real_cap > 0:
            remaining_for_attacker = max(0, real_cap - _spent_from_bots_today(attacker, now=now))
            clamped = _clamp_resources_to_budget(clamped, remaining_for_attacker)

    if (
        _resource_total(clamped) >= remaining_bot_budget
        and remaining_bot_budget > 0
        and profile.state not in {BotProfile.State.STALE, BotProfile.State.RETIRED}
    ):
        retire_virtual_player_if_unprotected(profile.pk, now=now)

    return clamped
