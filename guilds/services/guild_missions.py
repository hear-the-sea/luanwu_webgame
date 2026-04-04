from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any, cast

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from battle.execution import BattleOptions, execute_battle
from common.utils.celery import safe_apply_async
from core.exceptions import GuildMembershipError, GuildPermissionError, GuildValidationError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.services.battle_snapshots import build_guest_battle_snapshots, build_guest_snapshot_proxies
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.messages import bulk_create_messages

from ..models import (
    Guild,
    GuildBattleLineupEntry,
    GuildMember,
    GuildMissionRun,
    GuildMissionTemplate,
    GuildTroopStorage,
)
from . import guild_troops
from .technology import get_guild_dispatch_capacity, get_guild_lineup_capacity
from .warehouse import add_item_to_warehouse

logger = logging.getLogger(__name__)


def _normalize_positive_ids(raw_values: list[Any]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in raw_values or []:
        if raw is None or isinstance(raw, bool):
            raise GuildValidationError("门客参数错误")
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise GuildValidationError("门客参数错误") from exc
        if value <= 0:
            raise GuildValidationError("门客参数错误")
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _lock_manage_member(*, guild: Guild, operator, action_label: str) -> GuildMember:
    membership = (
        GuildMember.objects.select_for_update()
        .select_related("guild", "user")
        .filter(guild=guild, user=operator, is_active=True)
        .first()
    )
    if not membership:
        raise GuildMembershipError("您不在帮会中")
    if not membership.can_manage:
        raise GuildPermissionError(f"只有管理员/帮主可以{action_label}帮会任务")
    return membership


def _load_dispatch_lineup_rows(*, guild: Guild, pool_entry_ids: list[int]) -> list[GuildBattleLineupEntry]:
    row_map = {
        row.pool_entry_id: row
        for row in GuildBattleLineupEntry.objects.select_for_update()
        .select_related("pool_entry__owner_member__user__manor", "pool_entry__source_guest__template")
        .filter(guild=guild, pool_entry_id__in=pool_entry_ids)
        .order_by("slot_index", "id")
    }
    if len(row_map) != len(pool_entry_ids):
        raise GuildValidationError("每次出征只能从上阵门客中选取")

    ordered_rows: list[GuildBattleLineupEntry] = []
    for pool_entry_id in pool_entry_ids:
        row = row_map.get(pool_entry_id)
        if row is None or row.pool_entry.source_guest is None:
            raise GuildValidationError("每次出征只能从上阵门客中选取")
        ordered_rows.append(row)
    return ordered_rows


def _dispatch_countdown_for_run(run: GuildMissionRun) -> int:
    if run.return_at is None:
        raise RuntimeError("guild mission run missing return_at")
    remaining_seconds = math.ceil((run.return_at - timezone.now()).total_seconds())
    return max(0, remaining_seconds)


def parse_troop_loadout_from_post(post_data) -> dict[str, int]:
    loadout: dict[str, int] = {}
    for key, raw_value in getattr(post_data, "items", lambda: [])():
        if not isinstance(key, str) or not key.startswith("troop_"):
            continue
        troop_key = key.removeprefix("troop_").strip()
        if not troop_key:
            continue
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            quantity = int(raw_value)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            loadout[troop_key] = quantity
    return loadout


def get_guild_mission_page_context(member: GuildMember, *, selected_mission_key: str = "") -> dict[str, Any]:
    guild = member.guild
    now = timezone.now()
    refresh_due_guild_mission_runs(guild, now=now)
    active_run = (
        GuildMissionRun.objects.select_related("template", "started_by__user__manor")
        .filter(guild=guild, status=GuildMissionRun.Status.ACTIVE)
        .filter(Q(return_at__isnull=True) | Q(return_at__gt=now))
        .order_by("-started_at")
        .first()
    )
    mission_templates = list(GuildMissionTemplate.objects.filter(is_active=True).order_by("sort_weight", "id"))
    lineup_entries = list(
        GuildBattleLineupEntry.objects.filter(guild=guild)
        .select_related("pool_entry__source_guest__template", "pool_entry__owner_member__user__manor")
        .order_by("slot_index", "id")
    )
    troop_storages = list(
        GuildTroopStorage.objects.filter(guild=guild, count__gt=0)
        .select_related("troop_template")
        .order_by("troop_template__priority", "troop_template__id")
    )

    mission_groups = {
        "junior": [mission for mission in mission_templates if mission.difficulty == "junior"],
        "intermediate": [mission for mission in mission_templates if mission.difficulty == "intermediate"],
        "advanced": [mission for mission in mission_templates if mission.difficulty == "advanced"],
    }
    selected_mission = next(
        (mission for mission in mission_templates if mission.key == selected_mission_key),
        None,
    )
    active_tab = selected_mission.difficulty if selected_mission else "junior"
    return {
        "guild": guild,
        "member": member,
        "active_run": active_run,
        "mission_templates": mission_templates,
        "mission_groups": mission_groups,
        "selected_mission": selected_mission,
        "active_tab": active_tab,
        "lineup_entries": lineup_entries,
        "troop_storages": troop_storages,
        "dispatch_limit": get_guild_dispatch_capacity(guild),
        "lineup_limit": get_guild_lineup_capacity(guild),
    }


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


@transaction.atomic
def launch_guild_mission(
    *,
    guild: Guild,
    operator,
    template_key: str,
    pool_entry_ids: list[int],
    troop_loadout: dict[str, int],
) -> GuildMissionRun:
    locked_guild = Guild.objects.select_for_update().get(pk=guild.pk, is_active=True)
    membership = _lock_manage_member(guild=locked_guild, operator=operator, action_label="发起")
    refresh_due_guild_mission_runs(locked_guild)

    if (
        GuildMissionRun.objects.select_for_update()
        .filter(guild=locked_guild, status=GuildMissionRun.Status.ACTIVE)
        .exists()
    ):
        raise GuildValidationError("当前已有帮会任务进行中")

    template = GuildMissionTemplate.objects.filter(key=template_key, is_active=True).first()
    if template is None:
        raise GuildValidationError("帮会任务不存在")

    normalized_pool_entry_ids = _normalize_positive_ids(pool_entry_ids)
    if not normalized_pool_entry_ids:
        raise GuildValidationError("请选择至少一名上阵门客")

    lineup_rows = _load_dispatch_lineup_rows(guild=locked_guild, pool_entry_ids=normalized_pool_entry_ids)
    dispatch_limit = get_guild_dispatch_capacity(locked_guild)
    if len(lineup_rows) > dispatch_limit:
        raise GuildValidationError(f"本次最多只能派出 {dispatch_limit} 名门客")

    guests = [row.pool_entry.source_guest for row in lineup_rows if row.pool_entry.source_guest is not None]
    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)

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
        battle_at=now + timedelta(seconds=duration_seconds),
        return_at=now + timedelta(seconds=duration_seconds),
    )
    schedule_guild_mission_completion(run)
    return run


def request_retreat(*, run: GuildMissionRun, operator) -> None:
    now = timezone.now()
    overdue_guild: Guild | None = None

    with transaction.atomic():
        locked_run = GuildMissionRun.objects.select_for_update().select_related("guild").filter(pk=run.pk).first()
        if locked_run is None:
            raise GuildValidationError("帮会任务不存在")

        _lock_manage_member(guild=locked_run.guild, operator=operator, action_label="撤回")
        if locked_run.status != GuildMissionRun.Status.ACTIVE:
            raise GuildValidationError("当前任务不可撤回")

        if locked_run.return_at is not None and locked_run.return_at <= now:
            overdue_guild = locked_run.guild
        else:
            guild_troops.add_guild_troops(guild=locked_run.guild, loadout=locked_run.troop_loadout)
            locked_run.status = GuildMissionRun.Status.RETREATED
            locked_run.completed_at = now
            locked_run.save(update_fields=["status", "completed_at"])
            return

    if overdue_guild is not None:
        refresh_due_guild_mission_runs(overdue_guild, now=now)
    raise GuildValidationError("当前任务不可撤回")


def _resolve_guild_mission_attacker_limit(run: GuildMissionRun) -> int:
    candidate = int(getattr(run, "selected_guest_count", 0) or len(getattr(run, "guest_snapshots", []) or []))
    return max(1, candidate)


def _build_guild_mission_enemy_guest_keys(raw_enemy_guests) -> list[str | dict[str, Any]]:
    if raw_enemy_guests is None:
        return []
    if not isinstance(raw_enemy_guests, (list, tuple, set)):
        raise AssertionError(f"invalid guild mission enemy_guests payload: {raw_enemy_guests!r}")

    normalized: list[str | dict[str, Any]] = []
    for entry in raw_enemy_guests:
        if isinstance(entry, str):
            key = entry.strip()
            if not key:
                raise AssertionError(f"invalid guild mission enemy_guests entry: {entry!r}")
            normalized.append(key)
            continue

        if not isinstance(entry, dict):
            raise AssertionError(f"invalid guild mission enemy_guests entry: {entry!r}")

        raw_key = entry.get("key")
        if raw_key is None:
            raw_key = entry.get("template_key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise AssertionError(f"invalid guild mission enemy_guests entry: {entry!r}")

        normalized_entry: dict[str, Any] = {"key": raw_key.strip()}
        raw_skills = entry.get("skills")
        if raw_skills is not None:
            if not isinstance(raw_skills, (list, tuple, set)):
                raise AssertionError(f"invalid guild mission enemy_guests skills: {raw_skills!r}")
            normalized_entry["skills"] = [str(skill).strip() for skill in raw_skills if str(skill).strip()]
        normalized.append(normalized_entry)

    return normalized


def _build_guild_mission_defender_setup(run: GuildMissionRun) -> dict[str, Any]:
    return {
        "guest_keys": _build_guild_mission_enemy_guest_keys(getattr(run.template, "enemy_guests", None)),
        "troop_loadout": getattr(run.template, "enemy_troops", None) or {},
        "technology": getattr(run.template, "enemy_technology", None) or {},
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


@transaction.atomic
def finalize_guild_mission_run(run: GuildMissionRun, *, now=None) -> bool:
    finalized_at = now or timezone.now()
    locked_run = (
        GuildMissionRun.objects.select_for_update()
        .select_related("guild", "guild__founder", "template", "started_by__user")
        .filter(pk=run.pk)
        .first()
    )
    if locked_run is None or locked_run.status != GuildMissionRun.Status.ACTIVE:
        return False

    report_owner = ensure_manor(locked_run.started_by.user if locked_run.started_by else locked_run.guild.founder)
    guest_models = build_guest_snapshot_proxies(locked_run.guest_snapshots, include_guest_identity=True)
    battle_guest_models = cast(list[Any], guest_models)
    attacker_limit = _resolve_guild_mission_attacker_limit(locked_run)
    defender_setup = _build_guild_mission_defender_setup(locked_run)
    defender_limit = (
        max(1, len(defender_setup["guest_keys"])) if defender_setup["guest_keys"] else max(1, attacker_limit)
    )
    report = execute_battle(
        report_owner,
        battle_guest_models,
        battle_guest_models,
        BattleOptions(
            battle_type="guild_mission",
            troop_loadout=locked_run.troop_loadout,
            fill_default_troops=False,
            defender_setup=defender_setup,
            opponent_name=locked_run.template.name,
            auto_reward=False,
            send_message=False,
            apply_damage=False,
            validate_attacker_troop_capacity=False,
            limit=attacker_limit,
            defender_limit=defender_limit,
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
    _send_guild_mission_report_messages(locked_run, report)
    return True
