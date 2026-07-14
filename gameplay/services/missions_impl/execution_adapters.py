from __future__ import annotations

from typing import Any, Dict, List

from .enemy_guest_configs import normalize_enemy_guest_configs


def normalize_mapping(raw: Any) -> Dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid mission mapping payload: {raw!r}")
    normalized: Dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise AssertionError(f"invalid mission mapping key: {key!r}")
        key_str = key.strip()
        if not key_str:
            raise AssertionError(f"invalid mission mapping key: {key!r}")
        normalized[key_str] = value
    return normalized


def normalize_guest_configs(raw: Any) -> List[Any]:
    return list(normalize_enemy_guest_configs(raw))


def load_locked_mission_run(*, mission_run_model: Any, run_pk: int):
    from gameplay.models import Manor

    manor_id = mission_run_model.objects.filter(pk=run_pk).values_list("manor_id", flat=True).first()
    if manor_id is None:
        return None
    Manor.objects.select_for_update().get(pk=manor_id)
    return (
        mission_run_model.objects.select_for_update()
        .select_related("mission", "manor", "battle_report")
        .prefetch_related("guests")
        .filter(pk=run_pk)
        .first()
    )


def mark_run_completed(locked_run: Any, now: Any) -> None:
    locked_run.status = locked_run.Status.COMPLETED
    locked_run.completed_at = now
    locked_run.save(update_fields=["status", "completed_at"])


def build_mission_drops_with_salvage_adapter(
    locked_run: Any,
    report: Any,
    player_side: str,
    *,
    logger: Any,
    build_mission_drops_with_salvage,
    resolve_defense_drops_if_missing,
) -> Dict[str, int]:
    return build_mission_drops_with_salvage(
        locked_run,
        report,
        player_side,
        logger=logger,
        resolve_defense_drops_if_missing=resolve_defense_drops_if_missing,
    )
