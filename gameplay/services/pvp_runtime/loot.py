from __future__ import annotations

from typing import Any


def normalize_positive_int_mapping(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, int] = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        try:
            normalized_value = int(value)
        except (TypeError, ValueError):
            continue
        if normalized_value > 0:
            normalized[normalized_key] = normalized_value
    return normalized
