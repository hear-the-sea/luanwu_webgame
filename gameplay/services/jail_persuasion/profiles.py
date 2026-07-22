from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any

from django.conf import settings

from core.utils.jail_persuasion_copy_contract import PUBLISHED_COPY_KEYS
from core.utils.yaml_loader import load_yaml_data

logger = logging.getLogger(__name__)

METHOD_KINDNESS = "kindness"
METHOD_BRIBE = "bribe"
METHOD_REASON = "reason"
METHOD_MIGHT = "might"
METHOD_ORDER = (METHOD_KINDNESS, METHOD_BRIBE, METHOD_REASON, METHOD_MIGHT)
ALLOWED_COST_RESOURCES = {"silver", "grain", "gold_bar"}

ALLOWED_COPY_PLACEHOLDERS = {
    "prisoner_name",
    "speaker_name",
    "heart_delta",
    "affinity_delta",
    "speaker_loyalty_delta",
    "new_loyalty",
}

JAIL_PERSUASION_PROFILE_PATH = Path(settings.BASE_DIR) / "data" / "jail_persuasion_profiles.yaml"


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def round_half_up(value: int | float | Decimal) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_affinities(template: Any, *, captured_loyalty: int, original_level: int) -> dict[str, int]:
    morality = clamp(int(getattr(template, "default_morality", 50) or 0), 0, 100)
    loyalty = clamp(captured_loyalty, 0, 100)
    archetype = str(getattr(template, "archetype", "") or "")
    return {
        METHOD_KINDNESS: clamp(round_half_up(0.7 * morality + 0.3 * loyalty), 0, 100),
        METHOD_BRIBE: clamp(round_half_up(0.7 * (100 - morality) + 0.3 * (100 - loyalty)), 0, 100),
        METHOD_REASON: clamp(
            round_half_up((70 if archetype == "civil" else 40) + 0.2 * (100 - loyalty)),
            0,
            100,
        ),
        METHOD_MIGHT: clamp(
            round_half_up((70 if archetype == "military" else 40) + min(max(0, int(original_level)), 50) / 2),
            0,
            100,
        ),
    }


def stable_seed(*parts: object) -> int:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def _captured_at_seed(captured_at: datetime | object) -> str:
    if isinstance(captured_at, datetime):
        return captured_at.isoformat()
    return str(captured_at)


def choose_stance(
    *,
    prisoner_id: int,
    template_key: str,
    captured_at: datetime,
    scores: dict[str, int],
) -> str:
    ordered = sorted(
        METHOD_ORDER, key=lambda method: (-clamp(scores.get(method, 0), 0, 100), METHOD_ORDER.index(method))
    )
    candidates = ordered[:2]
    weights = [clamp(scores.get(method, 0), 0, 100) for method in candidates]
    total = sum(weights)
    if total <= 0:
        return candidates[0]
    choice = stable_seed(prisoner_id, template_key, _captured_at_seed(captured_at), "stance") % total
    return candidates[0] if choice < weights[0] else candidates[1]


def choose_taboo(scores: dict[str, int]) -> str:
    method = min(METHOD_ORDER, key=lambda item: (clamp(scores.get(item, 0), 0, 100), METHOD_ORDER.index(item)))
    return method if clamp(scores.get(method, 0), 0, 100) <= 39 else ""


def rarity_difficulty(template: Any) -> int:
    rarity = str(getattr(template, "rarity", "") or "")
    rules = load_jail_persuasion_profiles()["difficulty"]
    if rarity == "black" and bool(getattr(template, "is_hermit", False)):
        return int(rules["black_hermit"])
    return int(rules["rarity"].get(rarity, 0))


def validate_copy_placeholders(copy_key: str, text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"文案 {copy_key} 不能为空")
    fields = {field for _, field, _, _ in Formatter().parse(text) if field}
    unknown = fields - ALLOWED_COPY_PLACEHOLDERS
    if unknown:
        raise ValueError(f"文案 {copy_key} 存在未知占位符: {', '.join(sorted(unknown))}")


def _validate_copy_entry(entry: Any, *, context: str) -> dict[str, str]:
    if not isinstance(entry, dict):
        raise ValueError(f"{context} 必须是文案对象")
    key = str(entry.get("key") or "").strip()
    text = str(entry.get("text") or "").strip()
    if not key:
        raise ValueError(f"{context} 缺少文案键")
    validate_copy_placeholders(key, text)
    return {"key": key, "text": text}


def _validate_copy_list(value: Any, *, context: str, expected_count: int | None = None) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{context} 必须是文案列表")
    normalized = [_validate_copy_entry(item, context=f"{context}[{index}]") for index, item in enumerate(value)]
    if expected_count is not None and len(normalized) != expected_count:
        raise ValueError(f"{context} 必须配置 {expected_count} 条文案")
    if len({item["key"] for item in normalized}) != len(normalized):
        raise ValueError(f"{context} 存在重复文案键")
    return normalized


def _require_mapping(value: Any, *, context: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} 必须是对象")
    return value


def _reject_unknown_methods(value: dict[str, Any], *, context: str) -> None:
    unknown = {str(method) for method in value} - set(METHOD_ORDER)
    if unknown:
        raise ValueError(f"{context} 存在未知方法: {', '.join(sorted(unknown))}")


def _require_int(value: Any, *, context: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{context} 必须在 {minimum}..{maximum} 范围内")
    return value


def _require_number(value: Any, *, context: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} 必须是数值")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{context} 必须在 {minimum}..{maximum} 范围内")
    return normalized


def _iter_copy_keys(value: Any):
    if isinstance(value, dict):
        key = value.get("key")
        if isinstance(key, str) and ("text" in value or "prompt" in value):
            yield key
        for child in value.values():
            yield from _iter_copy_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_copy_keys(child)


def _validate_global_copy_keys(*sections: Any) -> None:
    keys = list(_iter_copy_keys(sections))
    seen: set[str] = set()
    duplicates: set[str] = set()
    for key in keys:
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        raise ValueError(f"存在重复文案键: {', '.join(sorted(duplicates))}")

    missing = PUBLISHED_COPY_KEYS - seen
    if missing:
        raise ValueError(f"缺少兼容文案键: {', '.join(sorted(missing))}")


def normalize_profiles(raw: Any) -> dict[str, Any]:
    root = _require_mapping(raw, context="招降配置")
    methods_raw = _require_mapping(root.get("methods"), context="methods")
    _reject_unknown_methods(methods_raw, context="methods")
    methods: dict[str, Any] = {}
    for method in METHOD_ORDER:
        item = _require_mapping(methods_raw.get(method), context=f"methods.{method}")
        cost = _require_mapping(item.get("cost", {}), context=f"methods.{method}.cost")
        unknown_resources = {str(resource) for resource in cost} - ALLOWED_COST_RESOURCES
        if unknown_resources:
            raise ValueError(f"methods.{method}.cost 存在未知资源: {', '.join(sorted(unknown_resources))}")
        methods[method] = {
            "label": str(item.get("label") or "").strip(),
            "cost": {
                str(resource): _require_int(
                    amount, context=f"methods.{method}.cost.{resource}", minimum=0, maximum=10_000_000
                )
                for resource, amount in cost.items()
            },
            "heart_delta": _require_int(
                item.get("heart_delta"), context=f"methods.{method}.heart_delta", minimum=-100, maximum=100
            ),
            "affinity_delta": _require_int(
                item.get("affinity_delta"), context=f"methods.{method}.affinity_delta", minimum=-100, maximum=100
            ),
        }
        if not methods[method]["label"]:
            raise ValueError(f"methods.{method}.label 不能为空")

    clues_raw = _require_mapping(root.get("clues"), context="clues")
    _reject_unknown_methods(clues_raw, context="clues")
    clues: dict[str, Any] = {}
    for method in METHOD_ORDER:
        item = _require_mapping(clues_raw.get(method), context=f"clues.{method}")
        clues[method] = {
            "subtle": _validate_copy_list(item.get("subtle"), context=f"clues.{method}.subtle", expected_count=2),
            "explicit": _validate_copy_list(item.get("explicit"), context=f"clues.{method}.explicit", expected_count=2),
        }

    feedback_raw = _require_mapping(root.get("feedback"), context="feedback")
    _reject_unknown_methods(feedback_raw, context="feedback")
    feedback: dict[str, Any] = {}
    for method in METHOD_ORDER:
        item = _require_mapping(feedback_raw.get(method), context=f"feedback.{method}")
        required_outcomes = ("matched", "neutral", "taboo") + (
            ("failed", "backfire") if method in {METHOD_REASON, METHOD_MIGHT} else ()
        )
        feedback[method] = {
            outcome: _validate_copy_list(item.get(outcome), context=f"feedback.{method}.{outcome}")
            for outcome in required_outcomes
        }
        if any(len(feedback[method][outcome]) < 3 for outcome in required_outcomes):
            raise ValueError(f"feedback.{method} 每种结果至少需要 3 条文案")

    milestones_raw = _require_mapping(root.get("milestones"), context="milestones")
    milestones: dict[str, Any] = {}
    for method in METHOD_ORDER:
        for stage, threshold in ((1, 35), (2, 70)):
            event_key = f"{method}_{threshold}"
            item = _require_mapping(milestones_raw.get(event_key), context=f"milestones.{event_key}")
            options = _require_mapping(item.get("options"), context=f"milestones.{event_key}.options")
            normalized_options: dict[str, Any] = {}
            for choice in ("aligned", "alternative"):
                option = _require_mapping(options.get(choice), context=f"milestones.{event_key}.options.{choice}")
                copy = _validate_copy_entry(option, context=f"milestones.{event_key}.options.{choice}")
                normalized_options[choice] = {
                    **copy,
                    "label": str(option.get("label") or "").strip(),
                    "heart_delta": _require_int(
                        option.get("heart_delta", 0),
                        context=f"milestones.{event_key}.{choice}.heart_delta",
                        minimum=-100,
                        maximum=100,
                    ),
                    "affinity_delta": _require_int(
                        option.get("affinity_delta", 0),
                        context=f"milestones.{event_key}.{choice}.affinity_delta",
                        minimum=-100,
                        maximum=100,
                    ),
                }
                if not normalized_options[choice]["label"]:
                    raise ValueError(f"milestones.{event_key}.{choice}.label 不能为空")
            prompt = _validate_copy_entry(
                {"key": str(item.get("key") or ""), "text": str(item.get("prompt") or "")},
                context=f"milestones.{event_key}.prompt",
            )
            milestones[event_key] = {
                "key": prompt["key"],
                "prompt": prompt["text"],
                "title": str(item.get("title") or "").strip(),
                "method": method,
                "stage": stage,
                "threshold": threshold,
                "options": normalized_options,
            }

    recruitment_raw = _require_mapping(root.get("recruitment_copy"), context="recruitment_copy")
    recruitment_copy = {
        mode: _validate_copy_list(recruitment_raw.get(mode), context=f"recruitment_copy.{mode}", expected_count=3)
        for mode in ("standard", "negotiated", "heartfelt")
    }

    daily_actions_raw = _require_mapping(root.get("daily_actions_by_jail_level"), context="daily_actions_by_jail_level")
    daily_actions = {
        level: _require_int(
            daily_actions_raw.get(level),
            context=f"daily_actions_by_jail_level.{level}",
            minimum=1,
            maximum=10,
        )
        for level in range(1, 6)
    }

    difficulty_raw = _require_mapping(root.get("difficulty"), context="difficulty")
    difficulty_rarity_raw = _require_mapping(difficulty_raw.get("rarity"), context="difficulty.rarity")
    difficulty = {
        "factor_per_point": _require_number(
            difficulty_raw.get("factor_per_point"),
            context="difficulty.factor_per_point",
            minimum=0.0,
            maximum=1.0,
        ),
        "minimum_factor": _require_number(
            difficulty_raw.get("minimum_factor"),
            context="difficulty.minimum_factor",
            minimum=0.0,
            maximum=1.0,
        ),
        "rarity": {
            rarity: _require_int(
                difficulty_rarity_raw.get(rarity),
                context=f"difficulty.rarity.{rarity}",
                minimum=0,
                maximum=8,
            )
            for rarity in ("black", "gray", "green", "red", "blue", "purple", "orange")
        },
        "black_hermit": _require_int(
            difficulty_raw.get("black_hermit"),
            context="difficulty.black_hermit",
            minimum=0,
            maximum=8,
        ),
    }

    recruitment_rules_raw = _require_mapping(root.get("recruitment"), context="recruitment")
    recruitment_rules: dict[str, Any] = {}
    for mode in ("standard", "negotiated", "heartfelt"):
        item = _require_mapping(recruitment_rules_raw.get(mode), context=f"recruitment.{mode}")
        recruitment_rules[mode] = {
            "heart_max": _require_int(
                item.get("heart_max"), context=f"recruitment.{mode}.heart_max", minimum=0, maximum=100
            ),
            "affinity_min": _require_int(
                item.get("affinity_min"), context=f"recruitment.{mode}.affinity_min", minimum=0, maximum=100
            ),
            "base_gold_cost": _require_int(
                item.get("base_gold_cost"), context=f"recruitment.{mode}.base_gold_cost", minimum=0, maximum=100
            ),
            "initial_loyalty": _require_int(
                item.get("initial_loyalty"), context=f"recruitment.{mode}.initial_loyalty", minimum=0, maximum=100
            ),
        }
    heartfelt_raw = _require_mapping(recruitment_rules_raw.get("heartfelt"), context="recruitment.heartfelt")
    recruitment_rules["heartfelt"].update(
        {
            "heart_cost_step": _require_int(
                heartfelt_raw.get("heart_cost_step"),
                context="recruitment.heartfelt.heart_cost_step",
                minimum=1,
                maximum=100,
            ),
            "milestone_discount": _require_int(
                heartfelt_raw.get("milestone_discount"),
                context="recruitment.heartfelt.milestone_discount",
                minimum=0,
                maximum=100,
            ),
        }
    )
    surcharge_raw = _require_mapping(
        recruitment_rules_raw.get("rarity_surcharge"), context="recruitment.rarity_surcharge"
    )
    recruitment_rules["rarity_surcharge"] = {
        rarity: _require_int(
            surcharge_raw.get(rarity),
            context=f"recruitment.rarity_surcharge.{rarity}",
            minimum=0,
            maximum=100,
        )
        for rarity in ("black", "gray", "green", "red", "blue", "purple", "orange")
    }
    recruitment_rules["black_hermit_surcharge"] = _require_int(
        recruitment_rules_raw.get("black_hermit_surcharge"),
        context="recruitment.black_hermit_surcharge",
        minimum=0,
        maximum=100,
    )

    _validate_global_copy_keys(clues, feedback, milestones, recruitment_copy)

    return {
        "methods": methods,
        "daily_actions_by_jail_level": daily_actions,
        "difficulty": difficulty,
        "recruitment": recruitment_rules,
        "clues": clues,
        "feedback": feedback,
        "milestones": milestones,
        "recruitment_copy": recruitment_copy,
    }


@lru_cache(maxsize=1)
def load_jail_persuasion_profiles() -> dict[str, Any]:
    raw = load_yaml_data(
        JAIL_PERSUASION_PROFILE_PATH,
        logger=logger,
        context="jail persuasion profiles",
        default={},
    )
    return normalize_profiles(raw)


def clear_jail_persuasion_profiles_cache() -> None:
    load_jail_persuasion_profiles.cache_clear()
    get_copy_index.cache_clear()


def get_clue_keys(
    *,
    stance_method: str,
    revealed_level: int,
    prisoner_id: int,
    template_key: str,
    captured_at: datetime,
) -> list[str]:
    level = clamp(revealed_level, 0, 3)
    if not stance_method or level == 0:
        return []
    clues = load_jail_persuasion_profiles()["clues"][stance_method]
    subtle = clues["subtle"]
    first_index = stable_seed(prisoner_id, template_key, _captured_at_seed(captured_at), "clue-subtle") % 2
    keys = [subtle[first_index]["key"]]
    if level >= 2:
        keys.append(subtle[1 - first_index]["key"])
    if level >= 3:
        explicit_index = stable_seed(prisoner_id, template_key, _captured_at_seed(captured_at), "clue-explicit") % 2
        keys.append(clues["explicit"][explicit_index]["key"])
    return keys


def _collect_copy_entries(value: Any, index: dict[str, str]) -> None:
    if isinstance(value, dict):
        if set(value) >= {"key", "text"} and isinstance(value["key"], str) and isinstance(value["text"], str):
            index[value["key"]] = value["text"]
        for child in value.values():
            _collect_copy_entries(child, index)
    elif isinstance(value, list):
        for child in value:
            _collect_copy_entries(child, index)


@lru_cache(maxsize=1)
def get_copy_index() -> dict[str, str]:
    index: dict[str, str] = {}
    _collect_copy_entries(load_jail_persuasion_profiles(), index)
    for event in load_jail_persuasion_profiles()["milestones"].values():
        index[event["key"]] = event["prompt"]
    return index


def render_copy(copy_key: str, params: dict[str, object] | None = None) -> str:
    template = get_copy_index().get(str(copy_key), "")
    if not template:
        return ""
    safe_params: dict[str, object] = {name: "" for name in ALLOWED_COPY_PLACEHOLDERS}
    safe_params.update(params or {})
    return template.format_map(safe_params)
