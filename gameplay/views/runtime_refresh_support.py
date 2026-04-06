from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.db import DatabaseError
from django.http import JsonResponse

from core.utils import json_error, json_success, sanitize_error_message


def _normalize_refresh_count(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, bool):
        return int(result)
    try:
        return int(result)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid refresh result: {result!r}") from exc


def run_refresh_api(
    *,
    operation: Callable[[], Any],
    logger_instance: logging.Logger,
    log_message: str,
    log_args: tuple[object, ...],
    payload_builder: Callable[[Any], dict[str, Any]] | None = None,
) -> JsonResponse:
    try:
        result = operation()
    except DatabaseError as exc:
        logger_instance.exception(log_message, *log_args)
        return json_error(sanitize_error_message(exc), status=500, include_message=True)

    payload = (
        payload_builder(result) if payload_builder is not None else {"refreshed": _normalize_refresh_count(result)}
    )
    return json_success(**payload)
