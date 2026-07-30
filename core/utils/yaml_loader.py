from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml


def load_yaml_data(
    path: str | Path,
    *,
    logger: logging.Logger,
    context: str,
    default: Any,
    raise_on_error: bool = False,
) -> Any:
    """
    Safely load YAML data from disk.

    Returns a deep-copied default value on any failure so callers can mutate
    the returned structure without leaking state across invocations.
    """
    resolved_path = Path(path)
    try:
        with resolved_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        if raise_on_error:
            raise
        logger.warning("%s file not found: %s", context, resolved_path)
        return copy.deepcopy(default)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        if raise_on_error:
            raise
        logger.exception("Failed to load %s from %s: %s", context, resolved_path, exc)
        return copy.deepcopy(default)

    if data is None:
        return copy.deepcopy(default)
    return data


def ensure_mapping(value: Any, *, logger: logging.Logger, context: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is not None:
        logger.warning("%s expected mapping but got %s", context, type(value).__name__)
    return {}


def ensure_list(value: Any, *, logger: logging.Logger, context: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is not None:
        logger.warning("%s expected list but got %s", context, type(value).__name__)
    return []
