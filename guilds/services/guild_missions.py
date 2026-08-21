from __future__ import annotations

import logging
import math
from datetime import timedelta
from math import isfinite
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from battle.execution import BattleOptions, execute_battle
from common.utils.celery import safe_apply_async
from core.exceptions import BattlePreparationError, GuildValidationError, InvalidBattleSnapshotError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.services.battle_snapshots import (
    build_guest_battle_snapshots,
    build_guest_snapshot_proxies,
    validate_battle_troop_loadout,
)
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.messages import bulk_create_messages
from guests.models import GuestTemplate

from ..models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate
from . import guild_troops
from .guild_dispatch import load_dispatch_lineup_rows, lock_manage_member, normalize_positive_ids
from .guild_mission_failure import fail_guild_mission_and_release_resources
from .technology import build_guild_troop_tech_levels, get_guild_dispatch_capacity
from .warehouse import add_item_to_warehouse

logger = logging.getLogger(__name__)


def _dispatch_countdown_for_run(run: GuildMissionRun) -> int:
    if run.return_at is None:
        raise RuntimeError("guild mission run missing return_at")
    remaining_seconds = math.ceil((run.return_at - timezone.now()).total_seconds())
    return max(0, remaining_seconds)


def schedule_guild_mission_completion(run: GuildMissionRun) -> None:
    from ..tasks import complete_guild_mission_task

    run_id = run.id

    def _dispatch_completion() -> None:
        dispatch_now = timezone.now()
        countdown = _dispatch_countdown_for_run(run)
        dispatched = safe_apply_async(
            complete_guild_mission_task,
            args=[run_id],
            countdown=countdown,
            logger=logger,
            log_message=f"guild mission completion dispatch failed: run_id={run_id}",
        )
        if not dispatched:
            if countdown == 0:
                logger.warning(
                    "guild mission completion dispatch failed for due run; finalizing synchronously: run_id=%s",
                    run_id,
                )
                finalize_guild_mission_run(run, now=dispatch_now)
                return
            logger.error(
                "guild mission completion dispatch returned False; relying on scan_due_guild_missions",
                extra={"task_name": "complete_guild_mission_task", "run_id": run_id},
            )

    transaction.on_commit(_dispatch_completion)


def refresh_due_guild_mission_runs(guild: Guild, *, now=None) -> int:
    finalized_at = now or timezone.now()
    due_runs = list(
        GuildMissionRun.objects.select_related("guild", "guild__founder", "template", "started_by__user")
        .filter(
            guild=guild,
            status=GuildMissionRun.Status.ACTIVE,
            return_at__isnull=False,
            return_at__lte=finalized_at,
        )
        .order_by("return_at", "id")
    )

    finalized_count = 0
    for due_run in due_runs:
        if finalize_guild_mission_run(due_run, now=finalized_at):
            finalized_count += 1
    return finalized_count


def launch_guild_mission(
    *,
    guild: Guild,
    operator,
    template_key: str,
    pool_entry_ids: list[int],
    troop_loadout: dict[str, int],
) -> GuildMissionRun:
    refresh_due_guild_mission_runs(guild)
    return _launch_guild_mission_atomic(
        guild=guild,
        operator=operator,
        template_key=template_key,
        pool_entry_ids=pool_entry_ids,
        troop_loadout=troop_loadout,
    )


@transaction.atomic
def _launch_guild_mission_atomic(
    *,
    guild: Guild,
    operator,
    template_key: str,
    pool_entry_ids: list[int],
    troop_loadout: dict[str, int],
) -> GuildMissionRun:
    locked_guild = Guild.objects.select_for_update().get(pk=guild.pk, is_active=True)
    membership = lock_manage_member(guild=locked_guild, operator=operator, permission_label="发起帮会任务")

    if (
        GuildMissionRun.objects.select_for_update()
        .filter(guild=locked_guild, status=GuildMissionRun.Status.ACTIVE)
        .exists()
    ):
        raise GuildValidationError("当前已有帮会任务进行中")

    template = GuildMissionTemplate.objects.filter(key=template_key, is_active=True).first()
    if template is None:
        raise GuildValidationError("帮会任务不存在")

    normalized_pool_entry_ids = normalize_positive_ids(pool_entry_ids)
    if not normalized_pool_entry_ids:
        raise GuildValidationError("请选择至少一名上阵门客")

    lineup_rows = load_dispatch_lineup_rows(guild=locked_guild, pool_entry_ids=normalized_pool_entry_ids)
    dispatch_limit = get_guild_dispatch_capacity(locked_guild)
    if len(lineup_rows) > dispatch_limit:
        raise GuildValidationError(f"本次最多只能派出 {dispatch_limit} 名门客")

    guests = [row.pool_entry.source_guest for row in lineup_rows if row.pool_entry.source_guest is not None]
    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
    attacker_troop_tech_snapshot = build_guild_troop_tech_levels(locked_guild)

    requested_troops = troop_loadout or {}
    if not template.allow_troops and requested_troops:
        raise GuildValidationError("该任务不可携带护院")
    normalized_troops = guild_troops.deduct_guild_troops(
        guild=locked_guild,
        loadout=requested_troops if template.allow_troops else {},
    )

    now = timezone.now()
    duration_seconds = template.actual_duration_seconds
    run = GuildMissionRun.objects.create(
        guild=locked_guild,
        template=template,
        started_by=membership,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=len(guests),
        ruby_reward=template.ruby_reward,
        guest_ids=[guest.id for guest in guests],
        guest_snapshots=guest_snapshots,
        troop_loadout=normalized_troops,
        attacker_troop_tech_snapshot=attacker_troop_tech_snapshot,
        battle_at=now + timedelta(seconds=duration_seconds),
        return_at=now + timedelta(seconds=duration_seconds),
    )
    schedule_guild_mission_completion(run)
    return run


def _lock_guild_root_for_mission_run(run_id: int) -> Guild | None:
    guild_id = GuildMissionRun.objects.filter(pk=run_id).values_list("guild_id", flat=True).first()
    if guild_id is None:
        return None
    return Guild.objects.select_for_update().get(pk=guild_id)


def _report_owner_user_id_for_mission_run(run_id: int) -> int | None:
    owner_ids = (
        GuildMissionRun.objects.filter(pk=run_id).values_list("started_by__user_id", "guild__founder_id").first()
    )
    if owner_ids is None:
        return None
    return owner_ids[0] or owner_ids[1]


def _ensure_report_owner_for_mission_run(run_id: int) -> int | None:
    owner_user_id = _report_owner_user_id_for_mission_run(run_id)
    if owner_user_id is None:
        logger.error("Guild mission report owner is unavailable: run_id=%s", run_id)
        return None
    if Manor.objects.filter(user_id=owner_user_id).exists():
        return owner_user_id

    user = get_user_model().objects.filter(pk=owner_user_id).first()
    if user is None:
        logger.error(
            "Guild mission report owner user is missing: run_id=%s user_id=%s",
            run_id,
            owner_user_id,
        )
        return None
    ensure_manor(user)
    return owner_user_id


def _lock_report_owner_manor_for_mission_run(run_id: int, *, owner_user_id: int) -> Manor | None:
    return Manor.objects.select_for_update().filter(user_id=owner_user_id).first()


def _locked_mission_report_owner_user_id(run: GuildMissionRun) -> int | None:
    started_by = run.started_by
    if started_by is not None:
        return started_by.user_id
    return run.guild.founder_id


def request_retreat(*, run: GuildMissionRun, operator) -> None:
    now = timezone.now()
    overdue_guild: Guild | None = None

    with transaction.atomic():
        locked_guild = _lock_guild_root_for_mission_run(run.pk)
        if locked_guild is None:
            raise GuildValidationError("帮会任务不存在")
        lock_manage_member(guild=locked_guild, operator=operator, permission_label="撤回帮会任务")
        locked_run = GuildMissionRun.objects.select_for_update().select_related("guild").filter(pk=run.pk).first()
        if locked_run is None:
            raise GuildValidationError("帮会任务不存在")

        if locked_run.status != GuildMissionRun.Status.ACTIVE:
            raise GuildValidationError("当前任务不可撤回")

        if locked_run.return_at is not None and locked_run.return_at <= now:
            overdue_guild = locked_run.guild
        else:
            guild_troops.add_guild_troops(guild=locked_guild, loadout=locked_run.troop_loadout)
            locked_run.status = GuildMissionRun.Status.RETREATED
            locked_run.completed_at = now
            locked_run.save(update_fields=["status", "completed_at"])
            return

    if overdue_guild is not None:
        refresh_due_guild_mission_runs(overdue_guild, now=now)
    raise GuildValidationError("当前任务不可撤回")


def can_retreat(run: GuildMissionRun, *, now=None) -> bool:
    if run.status != GuildMissionRun.Status.ACTIVE:
        return False

    resolved_now = now or timezone.now()
    return_at = getattr(run, "return_at", None)
    if return_at is None:
        return False
    return return_at > resolved_now


def _resolve_guild_mission_attacker_limit(run: GuildMissionRun) -> int:
    candidate = int(getattr(run, "selected_guest_count", 0) or len(getattr(run, "guest_snapshots", []) or []))
    return max(1, candidate)


def _invalid_guild_mission_config(message: str, *, field_name: str) -> InvalidBattleSnapshotError:
    return InvalidBattleSnapshotError(
        message,
        snapshot_kind="enemy_config",
        field_name=field_name,
    )


def _build_guild_mission_enemy_guest_keys(raw_enemy_guests) -> list[str | dict[str, Any]]:
    if raw_enemy_guests is None:
        return []
    if not isinstance(raw_enemy_guests, (list, tuple, set)):
        raise _invalid_guild_mission_config(
            "帮会任务敌方门客配置无效",
            field_name="enemy_guests",
        )

    normalized: list[str | dict[str, Any]] = []
    for entry in raw_enemy_guests:
        if isinstance(entry, str):
            key = entry.strip()
            if not key:
                raise _invalid_guild_mission_config(
                    "帮会任务敌方门客配置无效",
                    field_name="enemy_guests",
                )
            normalized.append(key)
            continue

        if not isinstance(entry, dict):
            raise _invalid_guild_mission_config(
                "帮会任务敌方门客配置无效",
                field_name="enemy_guests",
            )

        raw_key = entry.get("key")
        if raw_key is None:
            raw_key = entry.get("template_key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise _invalid_guild_mission_config(
                "帮会任务敌方门客配置无效",
                field_name="enemy_guests",
            )

        normalized_entry: dict[str, Any] = {"key": raw_key.strip()}
        raw_skills = entry.get("skills")
        if raw_skills is not None:
            if not isinstance(raw_skills, (list, tuple, set)):
                raise _invalid_guild_mission_config(
                    "帮会任务敌方门客技能配置无效",
                    field_name="enemy_guests",
                )
            normalized_skills: list[str] = []
            for skill in raw_skills:
                if not isinstance(skill, str) or not skill.strip():
                    raise _invalid_guild_mission_config(
                        "帮会任务敌方门客技能配置无效",
                        field_name="enemy_guests",
                    )
                normalized_skills.append(skill.strip())
            normalized_entry["skills"] = normalized_skills
        normalized.append(normalized_entry)

    guest_keys = {entry if isinstance(entry, str) else entry["key"] for entry in normalized}
    existing_guest_keys = set(GuestTemplate.objects.filter(key__in=guest_keys).values_list("key", flat=True))
    if guest_keys - existing_guest_keys:
        raise _invalid_guild_mission_config(
            "帮会任务敌方门客模板不存在",
            field_name="enemy_guests",
        )
    return normalized


def _normalize_guild_mission_enemy_troops(raw_enemy_troops: Any) -> dict[str, int]:
    if raw_enemy_troops is None:
        return {}
    if not isinstance(raw_enemy_troops, dict):
        raise _invalid_guild_mission_config(
            "帮会任务敌方护院配置无效",
            field_name="enemy_troops",
        )

    normalized: dict[str, int] = {}
    for raw_key, raw_value in raw_enemy_troops.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise _invalid_guild_mission_config(
                "帮会任务敌方护院配置无效",
                field_name="enemy_troops",
            )
        key = raw_key.strip()
        if key in normalized or raw_value is None or isinstance(raw_value, bool):
            raise _invalid_guild_mission_config(
                "帮会任务敌方护院配置无效",
                field_name="enemy_troops",
            )
        try:
            quantity = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise _invalid_guild_mission_config(
                "帮会任务敌方护院配置无效",
                field_name="enemy_troops",
            ) from exc
        if quantity < 0:
            raise _invalid_guild_mission_config(
                "帮会任务敌方护院配置无效",
                field_name="enemy_troops",
            )
        normalized[key] = quantity
    return normalized


def _normalize_guild_mission_enemy_technology(raw_enemy_technology: Any) -> dict[str, Any]:
    if raw_enemy_technology is None:
        return {}
    if not isinstance(raw_enemy_technology, dict):
        raise _invalid_guild_mission_config(
            "帮会任务敌方科技配置无效",
            field_name="enemy_technology",
        )

    guest_level = raw_enemy_technology.get("guest_level")
    if guest_level is not None:
        try:
            if isinstance(guest_level, bool) or int(guest_level) <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise _invalid_guild_mission_config(
                "帮会任务敌方门客等级配置无效",
                field_name="enemy_technology",
            ) from exc

    global_level = raw_enemy_technology.get("level")
    if global_level is not None:
        try:
            if isinstance(global_level, bool) or int(global_level) < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise _invalid_guild_mission_config(
                "帮会任务敌方科技等级配置无效",
                field_name="enemy_technology",
            ) from exc

    raw_levels = raw_enemy_technology.get("levels")
    if raw_levels is not None:
        if not isinstance(raw_levels, dict):
            raise _invalid_guild_mission_config(
                "帮会任务敌方科技等级配置无效",
                field_name="enemy_technology",
            )
        for key, value in raw_levels.items():
            try:
                if not isinstance(key, str) or not key.strip() or isinstance(value, bool) or int(value) < 0:
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise _invalid_guild_mission_config(
                    "帮会任务敌方科技等级配置无效",
                    field_name="enemy_technology",
                ) from exc

    raw_guest_skills = raw_enemy_technology.get("guest_skills")
    if raw_guest_skills is not None:
        if not isinstance(raw_guest_skills, (list, tuple, set)) or any(
            not isinstance(skill, str) or not skill.strip() for skill in raw_guest_skills
        ):
            raise _invalid_guild_mission_config(
                "帮会任务敌方门客技能配置无效",
                field_name="enemy_technology",
            )

    guest_bonus = raw_enemy_technology.get("guest_bonus")
    if guest_bonus is not None:
        try:
            if not isfinite(float(guest_bonus)) or float(guest_bonus) < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise _invalid_guild_mission_config(
                "帮会任务敌方属性加成配置无效",
                field_name="enemy_technology",
            ) from exc

    return dict(raw_enemy_technology)


def _normalize_guild_mission_attacker_tech_snapshot(raw_snapshot: Any) -> dict[str, int]:
    if raw_snapshot is None:
        return {}
    if not isinstance(raw_snapshot, dict):
        raise InvalidBattleSnapshotError(
            "帮会任务攻击方科技快照无效",
            snapshot_kind="attacker_technology",
            field_name="attacker_troop_tech_snapshot",
        )

    normalized: dict[str, int] = {}
    for raw_key, raw_value in raw_snapshot.items():
        if not isinstance(raw_key, str) or not raw_key.strip() or isinstance(raw_value, bool):
            raise InvalidBattleSnapshotError(
                "帮会任务攻击方科技快照无效",
                snapshot_kind="attacker_technology",
                field_name="attacker_troop_tech_snapshot",
            )
        key = raw_key.strip()
        if key in normalized:
            raise InvalidBattleSnapshotError(
                "帮会任务攻击方科技快照无效",
                snapshot_kind="attacker_technology",
                field_name="attacker_troop_tech_snapshot",
            )
        try:
            value = int(raw_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidBattleSnapshotError(
                "帮会任务攻击方科技快照无效",
                snapshot_kind="attacker_technology",
                field_name="attacker_troop_tech_snapshot",
            ) from exc
        if value < 0:
            raise InvalidBattleSnapshotError(
                "帮会任务攻击方科技快照无效",
                snapshot_kind="attacker_technology",
                field_name="attacker_troop_tech_snapshot",
            )
        normalized[key] = value
    return normalized


def _build_guild_mission_defender_setup(run: GuildMissionRun) -> dict[str, Any]:
    return {
        "guest_keys": _build_guild_mission_enemy_guest_keys(getattr(run.template, "enemy_guests", None)),
        "troop_loadout": _normalize_guild_mission_enemy_troops(getattr(run.template, "enemy_troops", None)),
        "technology": _normalize_guild_mission_enemy_technology(getattr(run.template, "enemy_technology", None)),
    }


def _send_guild_mission_report_messages(run: GuildMissionRun, report: Any) -> None:
    member_user_ids = list(
        GuildMember.objects.filter(guild=run.guild, is_active=True).values_list("user_id", flat=True)
    )
    if not member_user_ids:
        return

    user_to_manor = {manor.user_id: manor for manor in Manor.objects.filter(user_id__in=member_user_ids)}
    messages_data = [
        {
            "manor": manor,
            "kind": "battle",
            "title": f"{run.template.name} 战报",
            "body": "",
            "battle_report": report,
        }
        for user_id in member_user_ids
        if (manor := user_to_manor.get(user_id)) is not None
    ]
    if not messages_data:
        return

    try:
        bulk_create_messages(messages_data)
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception(
            "Guild mission report delivery failed: run_id=%s guild_id=%s recipient_count=%s",
            run.id,
            run.guild_id,
            len(messages_data),
        )


def _schedule_guild_mission_report_messages(run: GuildMissionRun, report: Any) -> None:
    def _send_report_messages_after_commit() -> None:
        _send_guild_mission_report_messages(run, report)

    transaction.on_commit(_send_report_messages_after_commit)


def finalize_guild_mission_run(run: GuildMissionRun, *, now=None) -> bool:
    owner_user_id = _ensure_report_owner_for_mission_run(run.pk)
    if owner_user_id is None:
        return False
    finalized_at = now or timezone.now()
    try:
        return _finalize_guild_mission_run_atomic(
            run,
            now=finalized_at,
            expected_report_owner_user_id=owner_user_id,
        )
    except InvalidBattleSnapshotError as exc:
        if exc.snapshot_kind == "troop_loadout":
            failure_reason = "invalid_troop_loadout"
        elif exc.snapshot_kind in {"enemy_config", "attacker_technology"}:
            failure_reason = "invalid_enemy_config"
        else:
            failure_reason = "invalid_guest_snapshot"
        return fail_guild_mission_and_release_resources(
            run.pk,
            failure_reason=failure_reason,
            now=finalized_at,
            failure_detail=str(exc),
        )
    except BattlePreparationError as exc:
        return fail_guild_mission_and_release_resources(
            run.pk,
            failure_reason="invalid_enemy_config",
            now=finalized_at,
            failure_detail=str(exc),
        )


@transaction.atomic
def _finalize_guild_mission_run_atomic(
    run: GuildMissionRun,
    *,
    now=None,
    expected_report_owner_user_id: int,
) -> bool:
    finalized_at = now or timezone.now()
    report_owner = _lock_report_owner_manor_for_mission_run(
        run.pk,
        owner_user_id=expected_report_owner_user_id,
    )
    if report_owner is None:
        return False
    locked_guild = _lock_guild_root_for_mission_run(run.pk)
    if locked_guild is None:
        return False
    locked_run = (
        GuildMissionRun.objects.select_for_update()
        .select_related("guild", "guild__founder", "template", "started_by__user")
        .filter(pk=run.pk)
        .first()
    )
    if locked_run is None or locked_run.status != GuildMissionRun.Status.ACTIVE:
        return False
    if _locked_mission_report_owner_user_id(locked_run) != expected_report_owner_user_id:
        logger.info(
            "Guild mission report owner changed during settlement; retrying from fresh state: run_id=%s",
            run.pk,
        )
        return False
    locked_run.guild = locked_guild

    guest_models = build_guest_snapshot_proxies(locked_run.guest_snapshots, include_guest_identity=True)
    battle_guest_models = cast(list[Any], guest_models)
    attacker_limit = _resolve_guild_mission_attacker_limit(locked_run)
    defender_setup = _build_guild_mission_defender_setup(locked_run)
    validated_troop_loadout = validate_battle_troop_loadout(locked_run.troop_loadout)
    attacker_tech_levels = _normalize_guild_mission_attacker_tech_snapshot(locked_run.attacker_troop_tech_snapshot)
    if not attacker_tech_levels:
        attacker_tech_levels = build_guild_troop_tech_levels(locked_run.guild)
    defender_limit = (
        max(1, len(defender_setup["guest_keys"])) if defender_setup["guest_keys"] else max(1, attacker_limit)
    )
    report = execute_battle(
        report_owner,
        battle_guest_models,
        battle_guest_models,
        BattleOptions(
            battle_type="guild_mission",
            troop_loadout=validated_troop_loadout,
            fill_default_troops=False,
            defender_setup=defender_setup,
            opponent_name=locked_run.template.name,
            auto_reward=False,
            send_message=False,
            apply_damage=False,
            validate_attacker_troop_capacity=False,
            limit=attacker_limit,
            defender_limit=defender_limit,
            attacker_tech_levels=attacker_tech_levels,
        ),
    )

    surviving_troops = guild_troops.calculate_surviving_guild_troops(locked_run.troop_loadout, report)
    guild_troops.add_guild_troops(guild=locked_run.guild, loadout=surviving_troops)
    if getattr(report, "winner", "") == "attacker" and locked_run.ruby_reward > 0:
        add_item_to_warehouse(locked_run.guild, "red_ruby", locked_run.ruby_reward, 0)

    locked_run.status = GuildMissionRun.Status.COMPLETED
    locked_run.completed_at = finalized_at
    locked_run.battle_report = report
    locked_run.save(update_fields=["status", "completed_at", "battle_report"])
    _schedule_guild_mission_report_messages(locked_run, report)
    return True
