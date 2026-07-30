from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TypeVar

from django.core.management.base import BaseCommand, CommandError, CommandParser


class OperationSummary(Protocol):
    @property
    def scanned(self) -> int: ...

    @property
    def locked(self) -> int: ...

    @property
    def changed(self) -> int: ...

    @property
    def skipped(self) -> int: ...

    @property
    def failed(self) -> int: ...

    @property
    def reasons(self) -> tuple[str, ...]: ...


_T = TypeVar("_T")
_CHECKSUM_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_SIMPLE_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_.:/+-]+\Z")
_MISSING = object()


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"contains non-finite value {value}")


def add_policy_version_argument(parser: CommandParser) -> None:
    option_actions = parser._option_string_actions  # type: ignore[attr-defined]
    django_version_action = option_actions.pop("--version", None)
    if django_version_action is not None:
        django_version_action.option_strings = ["--django-version"]
        django_version_action.dest = "django_version"
        option_actions["--django-version"] = django_version_action
    parser.add_argument("--version", "--policy-version", dest="version", type=int, required=True)


def positive_int(value: object, *, option_name: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CommandError(f"{option_name} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{option_name} must be a positive integer") from exc
    if normalized < 1 or (maximum is not None and normalized > maximum):
        suffix = f" between 1 and {maximum}" if maximum is not None else " a positive integer"
        raise CommandError(f"{option_name} must be{suffix}")
    return normalized


def non_negative_int(value: object, *, option_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CommandError(f"{option_name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise CommandError(f"{option_name} must be a non-negative integer") from exc
    if normalized < 0:
        raise CommandError(f"{option_name} must be a non-negative integer")
    return normalized


def checksum(value: object, *, option_name: str) -> str:
    normalized = str(value).strip().lower()
    if _CHECKSUM_PATTERN.fullmatch(normalized) is None:
        raise CommandError(f"{option_name} must be a 64-character hexadecimal SHA-256 checksum")
    return normalized


def non_empty_text(value: object, *, option_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise CommandError(f"{option_name} must not be blank")
    return normalized


def json_mappings(raw_values: Sequence[object], *, option_name: str) -> tuple[dict[str, Any], ...]:
    values: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_values):
        if isinstance(raw, Mapping):
            parsed: Any = dict(raw)
        else:
            try:
                parsed = json.loads(
                    str(raw),
                    object_pairs_hook=_strict_json_object,
                    parse_constant=_reject_json_constant,
                )
            except _DuplicateJsonKey as exc:
                raise CommandError(f"{option_name}[{index}] {exc}") from exc
            except json.JSONDecodeError as exc:
                raise CommandError(f"{option_name}[{index}] must be valid JSON") from exc
            except ValueError as exc:
                raise CommandError(f"{option_name}[{index}] {exc}") from exc
        if not isinstance(parsed, dict):
            raise CommandError(f"{option_name}[{index}] must be a JSON object")
        values.append(parsed)
    return tuple(values)


def invoke_application_service(operation: Callable[[], _T]) -> _T:
    try:
        return operation()
    except ValueError as exc:
        raise CommandError(str(exc)) from exc


def _format_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return str(value).lower()
    text = str(value)
    if _SIMPLE_VALUE_PATTERN.fullmatch(text) is not None:
        return text
    return json.dumps(text, ensure_ascii=True, sort_keys=True)


def write_operation_summary(
    command: BaseCommand,
    summary: OperationSummary,
    *,
    apply: bool,
    details: Sequence[tuple[str, object]] = (),
) -> None:
    reasons = tuple(sorted(str(reason) for reason in summary.reasons))
    fields: list[tuple[str, object]] = [
        ("mode", "apply" if apply else "dry-run"),
        ("scanned", summary.scanned),
        ("locked", summary.locked),
        ("changed", summary.changed),
        ("skipped", summary.skipped),
        ("failed", summary.failed),
        ("reasons", len(reasons)),
    ]
    last_profile_id = getattr(summary, "last_profile_id", _MISSING)
    if last_profile_id is not _MISSING:
        fields.append(("last_profile_id", last_profile_id))
    fields.extend(details)
    command.stdout.write(" ".join(f"{key}={_format_value(value)}" for key, value in fields))
    for reason in reasons:
        command.stdout.write(f"reason={_format_value(reason)}")
