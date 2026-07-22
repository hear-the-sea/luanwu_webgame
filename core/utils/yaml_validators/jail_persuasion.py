"""Validator for the jail persuasion profile and its copy library."""

from __future__ import annotations

from string import Formatter
from typing import Any, TypeGuard

from core.utils.jail_persuasion_copy_contract import PUBLISHED_COPY_KEYS

from .base import ValidationResult, _check_type

METHODS = ("kindness", "bribe", "reason", "might")
RARITIES = ("black", "gray", "green", "red", "blue", "purple", "orange")
RECRUITMENT_MODES = ("standard", "negotiated", "heartfelt")
ALLOWED_COST_RESOURCES = {"silver", "grain", "gold_bar"}
ALLOWED_PLACEHOLDERS = {
    "prisoner_name",
    "speaker_name",
    "heart_delta",
    "affinity_delta",
    "speaker_loyalty_delta",
    "new_loyalty",
}


def _mapping(value: Any, *, result: ValidationResult, file: str, path: str) -> TypeGuard[dict[Any, Any]]:
    if not isinstance(value, dict):
        result.add(file, path, "expected a mapping")
        return False
    return True


def _copy_list(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    minimum: int | None = None,
    exact: int | None = None,
    copy_keys: set[str],
) -> None:
    if not isinstance(value, list):
        result.add(file, path, "expected a list")
        return
    if exact is not None and len(value) != exact:
        result.add(file, path, f"expected exactly {exact} entries, got {len(value)}")
    if minimum is not None and len(value) < minimum:
        result.add(file, path, f"expected at least {minimum} entries, got {len(value)}")

    for index, entry in enumerate(value):
        entry_path = f"{path}[{index}]"
        if not isinstance(entry, dict):
            result.add(file, entry_path, "expected a mapping")
            continue
        key = entry.get("key")
        text = entry.get("text")
        if not isinstance(key, str) or not key.strip():
            result.add(file, entry_path, "field 'key' expected a non-empty string")
        elif key in copy_keys:
            result.add(file, entry_path, f"duplicate copy key '{key}'")
        else:
            copy_keys.add(key)
        if not isinstance(text, str) or not text.strip():
            result.add(file, entry_path, "field 'text' expected a non-empty string")
            continue
        try:
            fields = {field for _, field, _, _ in Formatter().parse(text) if field}
        except ValueError as exc:
            result.add(file, entry_path, f"invalid format string: {exc}")
            continue
        unknown = fields - ALLOWED_PLACEHOLDERS
        if unknown:
            result.add(file, entry_path, f"unknown placeholders: {', '.join(sorted(unknown))}")


def _int_field(
    value: Any, *, result: ValidationResult, file: str, path: str, field: str, minimum: int, maximum: int
) -> None:
    _check_type(value, int, result=result, file=file, path=path, field_name=field)
    if isinstance(value, bool) or not isinstance(value, int):
        return
    if not minimum <= value <= maximum:
        result.add(file, path, f"field '{field}' must be between {minimum} and {maximum}, got {value}")


def _unknown_methods(value: dict[Any, Any], *, result: ValidationResult, file: str, path: str) -> None:
    for method in sorted({str(method) for method in value} - set(METHODS)):
        result.add(file, path, f"unknown method '{method}'")


def validate_jail_persuasion_profiles(
    data: dict[str, Any], *, file: str = "jail_persuasion_profiles.yaml"
) -> ValidationResult:
    result = ValidationResult()
    copy_keys: set[str] = set()
    if not _mapping(data, result=result, file=file, path="<root>"):
        return result

    methods = data.get("methods")
    if _mapping(methods, result=result, file=file, path="methods"):
        _unknown_methods(methods, result=result, file=file, path="methods")
        for method in METHODS:
            path = f"methods.{method}"
            item = methods.get(method)
            if not _mapping(item, result=result, file=file, path=path):
                continue
            if not isinstance(item.get("label"), str) or not item["label"].strip():
                result.add(file, path, "field 'label' expected a non-empty string")
            cost = item.get("cost", {})
            if _mapping(cost, result=result, file=file, path=f"{path}.cost"):
                for resource, amount in cost.items():
                    if resource not in ALLOWED_COST_RESOURCES:
                        result.add(file, f"{path}.cost", f"unknown resource '{resource}'")
                    _int_field(
                        amount,
                        result=result,
                        file=file,
                        path=f"{path}.cost.{resource}",
                        field="value",
                        minimum=0,
                        maximum=10_000_000,
                    )
            _int_field(
                item.get("heart_delta"),
                result=result,
                file=file,
                path=path,
                field="heart_delta",
                minimum=-100,
                maximum=100,
            )
            _int_field(
                item.get("affinity_delta"),
                result=result,
                file=file,
                path=path,
                field="affinity_delta",
                minimum=-100,
                maximum=100,
            )

    daily = data.get("daily_actions_by_jail_level")
    if _mapping(daily, result=result, file=file, path="daily_actions_by_jail_level"):
        for level in range(1, 6):
            _int_field(
                daily.get(level),
                result=result,
                file=file,
                path="daily_actions_by_jail_level",
                field=str(level),
                minimum=1,
                maximum=10,
            )

    difficulty = data.get("difficulty")
    if _mapping(difficulty, result=result, file=file, path="difficulty"):
        for field, minimum, maximum in (("factor_per_point", 0, 1), ("minimum_factor", 0, 1)):
            value = difficulty.get(field)
            _check_type(value, (int, float), result=result, file=file, path="difficulty", field_name=field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
                result.add(file, "difficulty", f"field '{field}' must be between {minimum} and {maximum}")
        rarity = difficulty.get("rarity")
        if _mapping(rarity, result=result, file=file, path="difficulty.rarity"):
            for key in RARITIES:
                _int_field(
                    rarity.get(key), result=result, file=file, path="difficulty.rarity", field=key, minimum=0, maximum=8
                )
        _int_field(
            difficulty.get("black_hermit"),
            result=result,
            file=file,
            path="difficulty",
            field="black_hermit",
            minimum=0,
            maximum=8,
        )

    recruitment = data.get("recruitment")
    if _mapping(recruitment, result=result, file=file, path="recruitment"):
        for mode in RECRUITMENT_MODES:
            path = f"recruitment.{mode}"
            item = recruitment.get(mode)
            if not _mapping(item, result=result, file=file, path=path):
                continue
            for field in ("heart_max", "affinity_min", "base_gold_cost", "initial_loyalty"):
                _int_field(item.get(field), result=result, file=file, path=path, field=field, minimum=0, maximum=100)
            if mode == "heartfelt":
                _int_field(
                    item.get("heart_cost_step"),
                    result=result,
                    file=file,
                    path=path,
                    field="heart_cost_step",
                    minimum=1,
                    maximum=100,
                )
                _int_field(
                    item.get("milestone_discount"),
                    result=result,
                    file=file,
                    path=path,
                    field="milestone_discount",
                    minimum=0,
                    maximum=100,
                )
        surcharge = recruitment.get("rarity_surcharge")
        if _mapping(surcharge, result=result, file=file, path="recruitment.rarity_surcharge"):
            for rarity in RARITIES:
                _int_field(
                    surcharge.get(rarity),
                    result=result,
                    file=file,
                    path="recruitment.rarity_surcharge",
                    field=rarity,
                    minimum=0,
                    maximum=100,
                )
        _int_field(
            recruitment.get("black_hermit_surcharge"),
            result=result,
            file=file,
            path="recruitment",
            field="black_hermit_surcharge",
            minimum=0,
            maximum=100,
        )

    clues = data.get("clues")
    if _mapping(clues, result=result, file=file, path="clues"):
        _unknown_methods(clues, result=result, file=file, path="clues")
        for method in METHODS:
            item = clues.get(method)
            if not _mapping(item, result=result, file=file, path=f"clues.{method}"):
                continue
            for kind in ("subtle", "explicit"):
                _copy_list(
                    item.get(kind),
                    result=result,
                    file=file,
                    path=f"clues.{method}.{kind}",
                    exact=2,
                    copy_keys=copy_keys,
                )

    feedback = data.get("feedback")
    if _mapping(feedback, result=result, file=file, path="feedback"):
        _unknown_methods(feedback, result=result, file=file, path="feedback")
        for method in METHODS:
            item = feedback.get(method)
            if not _mapping(item, result=result, file=file, path=f"feedback.{method}"):
                continue
            outcomes = ("matched", "neutral", "taboo") + (
                ("failed", "backfire") if method in {"reason", "might"} else ()
            )
            for outcome in outcomes:
                _copy_list(
                    item.get(outcome),
                    result=result,
                    file=file,
                    path=f"feedback.{method}.{outcome}",
                    minimum=3,
                    copy_keys=copy_keys,
                )

    milestones = data.get("milestones")
    if _mapping(milestones, result=result, file=file, path="milestones"):
        for method in METHODS:
            for threshold in (35, 70):
                key = f"{method}_{threshold}"
                path = f"milestones.{key}"
                item = milestones.get(key)
                if not _mapping(item, result=result, file=file, path=path):
                    continue
                for field in ("key", "title", "prompt"):
                    if not isinstance(item.get(field), str) or not item[field].strip():
                        result.add(file, path, f"field '{field}' expected a non-empty string")
                _copy_list(
                    [{"key": item.get("key"), "text": item.get("prompt")}],
                    result=result,
                    file=file,
                    path=f"{path}.prompt",
                    exact=1,
                    copy_keys=copy_keys,
                )
                options = item.get("options")
                if _mapping(options, result=result, file=file, path=f"{path}.options"):
                    for choice in ("aligned", "alternative"):
                        option = options.get(choice)
                        option_path = f"{path}.options.{choice}"
                        if not _mapping(option, result=result, file=file, path=option_path):
                            continue
                        if not isinstance(option.get("label"), str) or not option["label"].strip():
                            result.add(file, option_path, "field 'label' expected a non-empty string")
                        _copy_list(
                            [option],
                            result=result,
                            file=file,
                            path=option_path,
                            exact=1,
                            copy_keys=copy_keys,
                        )
                        for field in ("heart_delta", "affinity_delta"):
                            _int_field(
                                option.get(field),
                                result=result,
                                file=file,
                                path=option_path,
                                field=field,
                                minimum=-100,
                                maximum=100,
                            )

    recruitment_copy = data.get("recruitment_copy")
    if _mapping(recruitment_copy, result=result, file=file, path="recruitment_copy"):
        for mode in RECRUITMENT_MODES:
            _copy_list(
                recruitment_copy.get(mode),
                result=result,
                file=file,
                path=f"recruitment_copy.{mode}",
                exact=3,
                copy_keys=copy_keys,
            )

    for key in sorted(PUBLISHED_COPY_KEYS - copy_keys):
        result.add(file, "<root>", f"missing compatibility copy key '{key}'")

    return result
