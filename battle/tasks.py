from __future__ import annotations

import logging
from typing import cast

from celery import shared_task
from django.apps import apps
from django.db import transaction

from core.exceptions import GameError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.services.missions_impl.enemy_guest_configs import EnemyGuestConfig, normalize_enemy_guest_configs
from guests.models import Guest

from .services import simulate_report

logger = logging.getLogger(__name__)


def _normalize_positive_int(raw, *, field_name: str, allow_none: bool = False, minimum: int = 1) -> int | None:
    if raw is None:
        if allow_none:
            return None
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    if isinstance(raw, bool):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}") from exc
    if value < minimum:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return value


def _normalize_battle_type(raw) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AssertionError(f"invalid mission battle_type: {raw!r}")
    return raw.strip()


def _normalize_guest_ids(raw) -> list[int]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid mission guest_ids: {raw!r}")
    return [cast(int, _normalize_positive_int(entry, field_name="guest_id")) for entry in raw]


def _normalize_enemy_technology_config(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        normalized: dict = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise AssertionError(f"invalid mission enemy technology key: {key!r}")
            key_str = key.strip()
            if not key_str:
                raise AssertionError(f"invalid mission enemy technology key: {key!r}")
            normalized[key_str] = value
        return normalized
    raise AssertionError(f"invalid mission enemy technology: {raw!r}")


def _normalize_mapping(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        normalized: dict = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise AssertionError(f"invalid mission mapping key: {key!r}")
            key_str = key.strip()
            if not key_str:
                raise AssertionError(f"invalid mission mapping key: {key!r}")
            normalized[key_str] = value
        return normalized
    raise AssertionError(f"invalid mission mapping payload: {raw!r}")


def _normalize_guest_configs(raw) -> list[EnemyGuestConfig]:
    return normalize_enemy_guest_configs(raw)


def _resolve_defender_max_squad(defender_setup: dict | None) -> int | None:
    if not defender_setup:
        return None
    guest_configs = _normalize_guest_configs(defender_setup.get("guest_keys"))
    return len(guest_configs) or None


def _normalize_troop_loadout(raw) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid mission troop loadout: {raw!r}")
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise AssertionError(f"invalid mission troop loadout key: {key!r}")
        key_str = key.strip()
        if not key_str:
            raise AssertionError(f"invalid mission troop loadout key: {key!r}")
        if isinstance(value, bool):
            raise AssertionError(f"invalid mission troop loadout quantity: {value!r}")
        try:
            qty = int(value)
        except (TypeError, ValueError):
            raise AssertionError(f"invalid mission troop loadout quantity: {value!r}") from None
        if qty < 0:
            raise AssertionError(f"invalid mission troop loadout quantity: {value!r}")
        normalized[key_str] = qty
    return normalized


def _coerce_enemy_guest_level(config: dict, default: int = 50) -> int:
    raw_level = config.get("guest_level", default)
    if isinstance(raw_level, bool):
        raise AssertionError(f"invalid mission enemy guest level: {raw_level!r}")
    try:
        level = int(raw_level)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission enemy guest level: {raw_level!r}") from exc
    if level <= 0:
        raise AssertionError(f"invalid mission enemy guest level: {raw_level!r}")
    return level


def _normalize_guest_skills(config: dict) -> list[str] | None:
    raw = config.get("guest_skills")
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid mission enemy guest skills: {raw!r}")
    skills: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise AssertionError(f"invalid mission enemy guest skills entry: {item!r}")
        key = item.strip()
        if not key:
            raise AssertionError(f"invalid mission enemy guest skills entry: {item!r}")
        skills.append(key)
    return skills or None


def _normalize_guest_snapshots_payload(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise AssertionError(f"invalid mission guest_snapshots payload: {raw!r}")
    normalized: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry:
            raise AssertionError(f"invalid mission guest_snapshot entry: {entry!r}")
        normalized.append(entry)
    return normalized


def _attach_report_to_mission_run(run_id: int, report_id: int) -> int | None:
    """Attach report atomically and return the effective report id on run."""
    MissionRun = apps.get_model("gameplay", "MissionRun")

    with transaction.atomic():
        row = MissionRun.objects.select_for_update().filter(pk=run_id).values("battle_report_id").first()
        if row is None:
            return None

        existing_report_id = row.get("battle_report_id")
        if existing_report_id:
            return int(existing_report_id)

        updated = MissionRun.objects.filter(pk=run_id, battle_report_id__isnull=True).update(battle_report_id=report_id)
        if updated:
            return int(report_id)

        fallback_report_id = MissionRun.objects.filter(pk=run_id).values_list("battle_report_id", flat=True).first()
        return int(fallback_report_id) if fallback_report_id else None


def _delete_report_if_unattached(report_id: int) -> None:
    MissionRun = apps.get_model("gameplay", "MissionRun")
    if MissionRun.objects.filter(battle_report_id=report_id).exists():
        return
    BattleReport = apps.get_model("battle", "BattleReport")
    BattleReport.objects.filter(pk=report_id).delete()


@shared_task(
    name="battle.generate_report",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=120,
    time_limit=180,
)
def generate_report_task(
    self,
    manor_id: int,
    mission_id: int | None,
    run_id: int | None,
    guest_ids: list[int],
    troop_loadout: dict,
    battle_type: str,
    fill_default_troops: bool = True,
    opponent_name: str | None = None,
    defender_setup: dict | None = None,
    drop_table: dict | None = None,
    travel_seconds: int | None = None,
    seed: int | None = None,
):
    """
    Generate a BattleReport asynchronously and attach it to MissionRun if provided.

    Features:
    - Automatic retry on failure (max 3 attempts)
    - Soft time limit (120s) and hard limit (180s)
    - Comprehensive error handling and logging
    """
    try:
        normalized_manor_id = cast(int, _normalize_positive_int(manor_id, field_name="manor_id"))
        normalized_mission_id = _normalize_positive_int(mission_id, field_name="mission_id", allow_none=True)
        normalized_run_id = _normalize_positive_int(run_id, field_name="run_id", allow_none=True)
        normalized_guest_ids = _normalize_guest_ids(guest_ids)
        normalized_battle_type = _normalize_battle_type(battle_type)
        normalized_troop_loadout = _normalize_troop_loadout(troop_loadout)
        normalized_travel_seconds = _normalize_positive_int(
            travel_seconds,
            field_name="travel_seconds",
            allow_none=True,
            minimum=0,
        )

        Manor = apps.get_model("gameplay", "Manor")
        MissionTemplate = apps.get_model("gameplay", "MissionTemplate")

        if normalized_run_id is not None:
            MissionRun = apps.get_model("gameplay", "MissionRun")

            row = MissionRun.objects.filter(pk=normalized_run_id).values("battle_report_id", "guest_snapshots").first()
            if row is None:
                logger.warning("MissionRun %s not found; skipping battle report generation", normalized_run_id)
                return None
            existing_report_id = row.get("battle_report_id")
            if existing_report_id:
                logger.info(
                    "MissionRun %s already has battle_report %s; skipping generation",
                    normalized_run_id,
                    existing_report_id,
                )
                return int(existing_report_id)
            guest_snapshots = _normalize_guest_snapshots_payload(row.get("guest_snapshots"))
        else:
            guest_snapshots = []

        # Fetch manor with error handling
        try:
            manor = Manor.objects.select_related("user").get(pk=normalized_manor_id)
        except Manor.DoesNotExist:
            logger.error("Manor %d not found for battle report generation", normalized_manor_id)
            # Don't retry if manor doesn't exist
            return None

        mission = MissionTemplate.objects.filter(pk=normalized_mission_id).first() if normalized_mission_id else None
        if guest_snapshots:
            from gameplay.services.battle_snapshots import build_guest_snapshot_proxies

            guests = build_guest_snapshot_proxies(guest_snapshots, include_guest_identity=True)
        else:
            guests = list(
                Guest.objects.filter(id__in=normalized_guest_ids).select_related("template").prefetch_related("skills")
            )
        battle_guests = cast(list[Guest], guests)

        if mission and mission.is_defense:
            from battle.combatants_pkg import build_named_ai_guests
            from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels

            tech_conf = _normalize_enemy_technology_config(mission.enemy_technology)
            attacker_guest_level = _coerce_enemy_guest_level(tech_conf)
            attacker_guests = build_named_ai_guests(
                _normalize_guest_configs(mission.enemy_guests), level=attacker_guest_level
            )
            attacker_tech_levels = resolve_enemy_tech_levels(tech_conf)
            attacker_guest_bonuses = get_guest_stat_bonuses(tech_conf)
            attacker_guest_skills = _normalize_guest_skills(tech_conf)
            enemy_troops = _normalize_troop_loadout(mission.enemy_troops)

            report = simulate_report(
                manor=manor,
                battle_type=normalized_battle_type,
                seed=seed,
                troop_loadout=enemy_troops,
                fill_default_troops=False,
                attacker_guests=attacker_guests,
                defender_setup={"troop_loadout": normalized_troop_loadout},
                defender_guests=battle_guests,
                defender_max_squad=len(guests) if guests else None,
                drop_table={},
                opponent_name=opponent_name or mission.name,
                travel_seconds=normalized_travel_seconds,
                send_message=False,
                auto_reward=False,
                drop_handler=None,
                max_squad=len(attacker_guests) if attacker_guests else None,
                apply_damage=False,
                use_lock=False,  # 修复：门客已DEPLOYED，不需要锁校验
                attacker_tech_levels=attacker_tech_levels,
                attacker_guest_bonuses=attacker_guest_bonuses or None,
                attacker_guest_skills=attacker_guest_skills,
                attacker_manor=None,
                validate_attacker_troop_capacity=False,
            )
        else:
            report = simulate_report(
                manor=manor,
                battle_type=normalized_battle_type,
                seed=seed,
                troop_loadout=normalized_troop_loadout,
                fill_default_troops=fill_default_troops,
                attacker_guests=battle_guests,
                defender_setup=_normalize_mapping(defender_setup),
                drop_table=_normalize_mapping(drop_table),
                opponent_name=opponent_name or (mission.name if mission else None),
                travel_seconds=normalized_travel_seconds,
                send_message=False,
                auto_reward=False,
                drop_handler=None,
                defender_max_squad=_resolve_defender_max_squad(defender_setup),
                max_squad=getattr(manor, "max_squad_size", None),
                apply_damage=False,
                use_lock=False,  # 修复：门客已DEPLOYED，不需要锁校验
            )

        if normalized_run_id is not None:
            effective_report_id = _attach_report_to_mission_run(normalized_run_id, int(report.pk))
            if effective_report_id != int(report.pk):
                _delete_report_if_unattached(int(report.pk))
                if effective_report_id is None:
                    logger.warning("MissionRun %s disappeared before attaching battle_report", normalized_run_id)
                    return None
                logger.info(
                    "MissionRun %s already attached report %s; dropping duplicate report %s",
                    normalized_run_id,
                    effective_report_id,
                    report.pk,
                )
                return int(effective_report_id)

        logger.info("Battle report %d generated successfully for manor %d", report.pk, normalized_manor_id)
        return report.pk

    except GameError as exc:
        # Business/validation errors should not be retried.
        logger.warning(
            "Battle report generation aborted (non-retriable) for manor %s: %s",
            normalized_manor_id,
            exc,
            extra={"manor_id": normalized_manor_id, "run_id": normalized_run_id, "mission_id": normalized_mission_id},
        )
        return None
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception(
            f"Battle report generation failed due to infrastructure error for manor {normalized_manor_id}: {exc}",
            extra={
                "manor_id": normalized_manor_id,
                "run_id": normalized_run_id,
                "mission_id": normalized_mission_id,
            },
        )
        # Retry on infrastructure errors.
        raise self.retry(exc=exc)
