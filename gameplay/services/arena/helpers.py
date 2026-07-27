from __future__ import annotations

import random
from datetime import timedelta
from typing import Any, Iterable

from django.conf import settings
from django.utils import timezone

from core.exceptions import ArenaGuestSelectionError
from core.utils.time_scale import scale_duration

from .rewards import ArenaRandomItemOption


def load_positive_int_setting(name: str, default: int, *, minimum: int = 1) -> int:
    sentinel = object()
    raw_value = getattr(settings, name, sentinel)
    if raw_value is sentinel:
        return default
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid arena setting {name}: {raw_value!r}")
    raw_value_for_int: Any = raw_value
    try:
        parsed_value = int(raw_value_for_int)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid arena setting {name}: {raw_value!r}") from exc
    if parsed_value < minimum:
        raise AssertionError(f"invalid arena setting {name}: {raw_value!r}")
    return parsed_value


def normalize_guest_ids(guest_ids: Iterable[int], *, max_guests_per_entry: int) -> list[int]:
    seen: set[int] = set()
    normalized: list[int] = []
    for raw in guest_ids:
        try:
            guest_id = int(raw)
        except (TypeError, ValueError):
            raise ArenaGuestSelectionError("门客选择有误")
        if guest_id <= 0:
            raise ArenaGuestSelectionError("门客选择有误")
        if guest_id in seen:
            continue
        seen.add(guest_id)
        normalized.append(guest_id)

    if not normalized:
        raise ArenaGuestSelectionError("请至少选择 1 名门客")
    if len(normalized) > max_guests_per_entry:
        raise ArenaGuestSelectionError(f"每次最多选择 {max_guests_per_entry} 名门客")
    return normalized


def round_interval_seconds(base_seconds: int) -> int:
    return max(1, scale_duration(base_seconds, minimum=1))


def today_bounds(*, now=None):
    current_time = timezone.localtime(now or timezone.now())
    start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def current_week_bounds(*, now=None):
    current_time = timezone.localtime(now or timezone.now())
    day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day_start - timedelta(days=day_start.weekday())
    end = start + timedelta(days=7)
    return start, end


def today_local_date(*, now=None):
    return timezone.localdate(now or timezone.now())


def choose_random_item_option(
    options: tuple[ArenaRandomItemOption, ...],
    *,
    rng: random.Random | random.SystemRandom,
) -> ArenaRandomItemOption | None:
    if not options:
        return None
    total_weight = sum(max(0, int(option.weight)) for option in options)
    if total_weight <= 0:
        return None
    roll = rng.random() * total_weight
    chosen = options[-1]
    cumulative = 0
    for option in options:
        cumulative += option.weight
        if roll < cumulative:
            chosen = option
            break
    return chosen


def resolve_random_reward_items(
    options: tuple[ArenaRandomItemOption, ...],
    quantity: int,
    *,
    rng: random.Random | random.SystemRandom,
) -> dict[str, int]:
    grants: dict[str, int] = {}
    rounds = max(0, int(quantity or 0))
    for _ in range(rounds):
        chosen = choose_random_item_option(options, rng=rng)
        if chosen is None:
            break
        grants[chosen.item_key] = grants.get(chosen.item_key, 0) + chosen.amount
    return grants


def round_interval_delta(round_interval_seconds: int) -> timedelta:
    return timedelta(seconds=max(1, int(round_interval_seconds)))


def build_round_pairings(
    entry_ids: list[int], *, rng: random.Random | random.SystemRandom | None = None
) -> list[tuple[int, int | None]]:
    shuffled_ids = entry_ids[:]
    (rng or random.SystemRandom()).shuffle(shuffled_ids)
    pairings: list[tuple[int, int | None]] = []
    iterator = iter(shuffled_ids)
    for attacker_id in iterator:
        defender_id = next(iterator, None)
        pairings.append((attacker_id, defender_id))
    return pairings


def calculate_ranked_entries(entries: list, winner_entry) -> list:
    winner = winner_entry
    if winner is None and entries:
        winner = sorted(entries, key=lambda item: (-item.matches_won, -(item.eliminated_round or 0), item.id))[0]

    ranked: list = []
    if winner:
        ranked.append(winner)

    others = [entry for entry in entries if winner is None or entry.pk != winner.pk]
    others.sort(key=lambda item: (-(item.eliminated_round or 0), -item.matches_won, item.id))
    ranked.extend(others)
    return ranked


def reward_for_rank(rank: int, *, base_participation_coins: int, rank_bonus_coins: dict[int, int]) -> int:
    return base_participation_coins + rank_bonus_coins.get(rank, 0)


def collect_round_outcome_entry_ids(round_matches: list) -> tuple[list[int], list[int]]:
    winner_ids: list[int] = []
    loser_ids: list[int] = []
    for match in round_matches:
        winner_id = match.winner_entry_id
        if not winner_id:
            continue
        winner_ids.append(winner_id)
        if match.defender_entry_id is None:
            continue
        if winner_id == match.attacker_entry_id:
            loser_id = match.defender_entry_id
        else:
            loser_id = match.attacker_entry_id
        if loser_id is not None:
            loser_ids.append(loser_id)
    return list(dict.fromkeys(winner_ids)), list(dict.fromkeys(loser_ids))
