import copy
import logging
from datetime import timedelta
from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db.models import Count, Max

from core.game_data.arena_ring_rules import DEFAULT_RULES as DEFAULT_RULES
from core.utils.yaml_loader import load_yaml_data

RING_DEFINITIONS = (("qinglong", "青龙擂"), ("baihu", "白虎擂"))


@lru_cache(maxsize=1)
def _cached_ring_rules() -> dict[str, Any]:
    raw = load_yaml_data(
        Path(settings.BASE_DIR) / "data" / "arena_ring_rules.yaml",
        logger=logging.getLogger(__name__),
        context="arena_ring_rules",
        default=DEFAULT_RULES,
    )
    rules = dict(DEFAULT_RULES)
    if isinstance(raw, dict):
        for key, default in DEFAULT_RULES.items():
            value = raw.get(key, default)
            minimum = 0 if key == "cooldown_seconds" else 1
            if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
                rules[key] = value
    if (
        rules["inspection_income"] + rules["inspection_fee"] != rules["inspection_cost"]
        or rules["inspection_fee"] * 10 != rules["inspection_cost"]
    ):
        for key in ("inspection_cost", "inspection_income", "inspection_fee"):
            rules[key] = DEFAULT_RULES[key]
    return {
        **rules,
        "rings": RING_DEFINITIONS,
        "streak_tiers": [(count, rules[f"streak_{count}_coins"]) for count in (3, 5, 10)],
        "chunqiu_streak_tiers": [(count, rules[f"streak_{count}_chunqiu_coins"]) for count in (3, 5, 10)],
    }


def load_ring_rules() -> dict[str, Any]:
    return copy.deepcopy(_cached_ring_rules())


def clear_ring_rules_cache() -> None:
    _cached_ring_rules.cache_clear()


def ring_challenge_state(tenure: Any, manor: Any, now: Any) -> dict[str, Any]:
    rules = load_ring_rules()
    matches = tenure.matches.filter(attacker_id=manor.pk).aggregate(count=Count("id"), latest=Max("created_at"))
    remaining = max(0, rules["max_tenure_challenges"] - matches["count"])
    ready_at = matches["latest"] + timedelta(seconds=rules["challenge_cooldown_seconds"]) if matches["latest"] else now
    cooldown = max(0, ceil((ready_at - now).total_seconds()))
    return {
        "tenure_challenges_remaining": remaining,
        "challenge_ready_at": ready_at,
        "challenge_cooldown": cooldown,
        "challenge_cooldown_minutes": ceil(cooldown / 60),
        "can_challenge": remaining > 0 and cooldown == 0,
    }
