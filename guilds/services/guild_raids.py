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
from core.exceptions import GuildValidationError
from gameplay.services.battle_salvage import calculate_battle_salvage
from gameplay.services.battle_snapshots import build_guest_battle_snapshots, build_guest_snapshot_proxies
from gameplay.services.manor.core import ensure_manor

from .. import constants as guild_constants
from ..models import Guild, GuildBattleLineupEntry, GuildRaidRun
from . import guild_troops
from .guild_dispatch import load_dispatch_lineup_rows, lock_manage_member, normalize_positive_ids
from .guild_raid_loot import grant_guild_raid_battle_rewards, transfer_guild_raid_loot
from .guild_raid_messages import send_guild_raid_report_messages, send_guild_raid_warning_messages
from .guild_raid_rules import (
    calculate_guild_raid_travel_time,
    can_attack_guild,
    get_guild_battle_block_reason,
    reset_guild_pvp_counters_if_needed,
)
from .technology import get_guild_dispatch_capacity

logger = logging.getLogger(__name__)
IN_FLIGHT_GUILD_RAID_STATUSES = (
    GuildRaidRun.Status.MARCHING,
    GuildRaidRun.Status.BATTLING,
    GuildRaidRun.Status.RETURNING,
    GuildRaidRun.Status.RETREATED,
)


def _next_due_at(run: GuildRaidRun):
    if run.status == GuildRaidRun.Status.MARCHING:
        return run.battle_at
    if run.status in (GuildRaidRun.Status.RETURNING, GuildRaidRun.Status.RETREATED):
        return run.return_at
    return None


def _dispatch_countdown_for_run(run: GuildRaidRun) -> int:
    due_at = _next_due_at(run)
    if due_at is None:
        raise RuntimeError("guild raid run missing due time")
    remaining_seconds = math.ceil((due_at - timezone.now()).total_seconds())
    return max(0, remaining_seconds)


def _load_defender_guests(defender_guild: Guild) -> list[Any]:
    guests: list[Any] = []
    lineup_rows = (
        GuildBattleLineupEntry.objects.filter(guild=defender_guild)
        .select_related("pool_entry__source_guest__template", "pool_entry__source_guest__manor")
        .order_by("slot_index", "id")
    )
    for row in lineup_rows:
        guest = row.pool_entry.source_guest
        if guest is not None:
            guests.append(guest)
    return guests


def _apply_guild_defeat_protection(defender_guild: Guild, *, now=None) -> None:
    now = now or timezone.now()
    duration_seconds = int(guild_constants.GUILD_PVP_DEFEAT_PROTECTION_SECONDS or 0)
    if duration_seconds <= 0:
        return

    new_until = now + timedelta(seconds=duration_seconds)
    current_until = defender_guild.defeat_protection_until
    if current_until and current_until > new_until:
        new_until = current_until
    defender_guild.defeat_protection_until = new_until
    defender_guild.save(update_fields=["defeat_protection_until"])


def schedule_guild_raid_completion(run: GuildRaidRun) -> None:
    from ..tasks import complete_guild_raid_task

    run_id = run.id

    def _dispatch_completion() -> None:
        dispatch_now = timezone.now()
        countdown = _dispatch_countdown_for_run(run)
        dispatched = safe_apply_async(
            complete_guild_raid_task,
            args=[run_id],
            countdown=countdown,
            logger=logger,
            log_message=f"guild raid completion dispatch failed: run_id={run_id}",
        )
        if dispatched:
            return
        if countdown == 0:
            process_due_guild_raid(run, now=dispatch_now)
            return
        logger.error(
            "guild raid completion dispatch returned False; relying on scan_due_guild_raids",
            extra={"task_name": "complete_guild_raid_task", "run_id": run_id},
        )

    transaction.on_commit(_dispatch_completion)


def refresh_due_guild_raids(guild: Guild, *, now=None, include_incoming_marching: bool = False) -> int:
    processed_at = now or timezone.now()
    due_filter = Q(
        attacker_guild=guild,
        status=GuildRaidRun.Status.MARCHING,
        battle_at__isnull=False,
        battle_at__lte=processed_at,
    ) | Q(
        attacker_guild=guild,
        status__in=[GuildRaidRun.Status.RETURNING, GuildRaidRun.Status.RETREATED],
        return_at__isnull=False,
        return_at__lte=processed_at,
    )
    if include_incoming_marching:
        due_filter |= Q(
            defender_guild=guild,
            status=GuildRaidRun.Status.MARCHING,
            battle_at__isnull=False,
            battle_at__lte=processed_at,
        )
    due_runs = list(
        GuildRaidRun.objects.select_related(
            "attacker_guild",
            "attacker_guild__founder",
            "defender_guild",
            "defender_guild__founder",
            "started_by__user",
            "battle_report",
        )
        .filter(due_filter)
        .order_by("battle_at", "return_at", "id")
    )
    processed_count = 0
    for due_run in due_runs:
        if process_due_guild_raid(due_run, now=processed_at):
            processed_count += 1
    return processed_count


def prepare_guild_pvp_read_state(guild: Guild, *, now=None) -> None:
    refresh_due_guild_raids(guild, now=now, include_incoming_marching=True)


@transaction.atomic
def start_guild_raid(
    *,
    guild: Guild,
    defender_guild: Guild,
    operator,
    pool_entry_ids: list[int],
    troop_loadout: dict[str, int],
) -> GuildRaidRun:
    locked_guild = Guild.objects.select_for_update().get(pk=guild.pk, is_active=True)
    locked_defender = Guild.objects.select_for_update().get(pk=defender_guild.pk, is_active=True)
    if locked_guild.pk == locked_defender.pk:
        raise GuildValidationError("不能进攻自己的帮会")

    today = timezone.localdate()
    reset_guild_pvp_counters_if_needed(locked_guild, today=today)
    reset_guild_pvp_counters_if_needed(locked_defender, today=today)
    can_attack, blocked_reason = can_attack_guild(attacker_guild=locked_guild, defender_guild=locked_defender)
    if not can_attack:
        raise GuildValidationError(blocked_reason or "当前不可发起帮会攻击")

    membership = lock_manage_member(guild=locked_guild, operator=operator, permission_label="发起帮会攻击")
    refresh_due_guild_raids(locked_guild)

    if (
        GuildRaidRun.objects.select_for_update()
        .filter(
            attacker_guild=locked_guild,
            status__in=IN_FLIGHT_GUILD_RAID_STATUSES,
        )
        .exists()
    ):
        raise GuildValidationError("当前已有帮会 PVP 出征中")

    normalized_pool_entry_ids = normalize_positive_ids(pool_entry_ids)
    if not normalized_pool_entry_ids:
        raise GuildValidationError("请选择至少一名上阵门客")

    lineup_rows = load_dispatch_lineup_rows(guild=locked_guild, pool_entry_ids=normalized_pool_entry_ids)
    dispatch_limit = get_guild_dispatch_capacity(locked_guild)
    if len(lineup_rows) > dispatch_limit:
        raise GuildValidationError(f"本次最多只能派出 {dispatch_limit} 名门客")

    guests = [row.pool_entry.source_guest for row in lineup_rows if row.pool_entry.source_guest is not None]
    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
    normalized_troops = guild_troops.deduct_guild_troops(guild=locked_guild, loadout=troop_loadout or {})
    travel_time = calculate_guild_raid_travel_time(guests, normalized_troops)
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=locked_guild,
        defender_guild=locked_defender,
        started_by=membership,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=len(guests),
        guest_ids=[guest.id for guest in guests],
        guest_snapshots=guest_snapshots,
        troop_loadout=normalized_troops,
        travel_time=travel_time,
        started_at=now,
        battle_at=now + timedelta(seconds=travel_time),
        return_at=now + timedelta(seconds=travel_time * 2),
    )
    locked_guild.silver = max(0, int(locked_guild.silver or 0) - guild_constants.GUILD_PVP_FIXED_ATTACK_COST_SILVER)
    locked_guild.pvp_attack_count_today = int(locked_guild.pvp_attack_count_today or 0) + 1
    guild_update_fields = ["silver", "pvp_attack_count_today"]
    if locked_guild.defeat_protection_until and locked_guild.defeat_protection_until > now:
        locked_guild.defeat_protection_until = None
        guild_update_fields.append("defeat_protection_until")
    locked_guild.save(update_fields=guild_update_fields)
    locked_defender.pvp_defense_count_today = int(locked_defender.pvp_defense_count_today or 0) + 1
    locked_defender.save(update_fields=["pvp_defense_count_today"])
    schedule_guild_raid_completion(run)
    send_guild_raid_warning_messages(run)
    return run


@transaction.atomic
def request_retreat(*, run: GuildRaidRun, operator) -> None:
    now = timezone.now()
    overdue_guild: Guild | None = None

    locked_run = GuildRaidRun.objects.select_for_update().select_related("attacker_guild").filter(pk=run.pk).first()
    if locked_run is None:
        raise GuildValidationError("帮会出征不存在")

    lock_manage_member(guild=locked_run.attacker_guild, operator=operator, permission_label="撤回帮会攻击")
    if locked_run.status != GuildRaidRun.Status.MARCHING:
        raise GuildValidationError("当前出征不可撤回")

    if locked_run.battle_at is not None and locked_run.battle_at <= now:
        overdue_guild = locked_run.attacker_guild
    else:
        locked_run.status = GuildRaidRun.Status.RETREATED
        locked_run.blocked_reason = "主动撤回"
        locked_run.save(update_fields=["status", "blocked_reason"])
        schedule_guild_raid_completion(locked_run)
        return

    if overdue_guild is not None:
        refresh_due_guild_raids(overdue_guild, now=now)
    raise GuildValidationError("当前出征不可撤回")


@transaction.atomic
def process_guild_raid_battle(run: GuildRaidRun, *, now=None) -> bool:
    processed_at = now or timezone.now()
    locked_run = (
        GuildRaidRun.objects.select_for_update()
        .select_related(
            "attacker_guild",
            "attacker_guild__founder",
            "defender_guild",
            "defender_guild__founder",
            "started_by__user",
        )
        .filter(pk=run.pk)
        .first()
    )
    if locked_run is None or locked_run.status != GuildRaidRun.Status.MARCHING:
        return False
    if locked_run.battle_at is not None and locked_run.battle_at > processed_at:
        return False

    defender_locked = Guild.objects.select_for_update().get(pk=locked_run.defender_guild_id)
    blocked_reason = get_guild_battle_block_reason(defender_guild=defender_locked, now=processed_at)
    if blocked_reason:
        locked_run.status = GuildRaidRun.Status.RETREATED
        locked_run.blocked_reason = blocked_reason
        locked_run.save(update_fields=["status", "blocked_reason"])
        schedule_guild_raid_completion(locked_run)
        return True

    locked_run.status = GuildRaidRun.Status.BATTLING
    locked_run.save(update_fields=["status"])

    report_owner_user = locked_run.started_by.user if locked_run.started_by else locked_run.attacker_guild.founder
    report_owner = ensure_manor(report_owner_user)
    guest_models = build_guest_snapshot_proxies(locked_run.guest_snapshots, include_guest_identity=True)
    battle_guest_models = cast(list[Any], guest_models)
    attacker_limit = max(1, int(getattr(locked_run, "selected_guest_count", 0) or len(battle_guest_models)))
    defender_guests = _load_defender_guests(defender_locked)
    defender_setup = guild_troops.build_guild_defender_setup(guild=defender_locked)
    defender_limit = max(1, len(defender_guests) if defender_guests else attacker_limit)
    report = execute_battle(
        report_owner,
        battle_guest_models,
        battle_guest_models,
        BattleOptions(
            battle_type="guild_raid",
            troop_loadout=locked_run.troop_loadout,
            fill_default_troops=False,
            defender_guests=defender_guests or None,
            defender_setup=defender_setup or None,
            opponent_name=defender_locked.name,
            auto_reward=False,
            send_message=False,
            apply_damage=False,
            validate_attacker_troop_capacity=False,
            limit=attacker_limit,
            defender_limit=defender_limit,
        ),
    )
    if defender_setup:
        guild_troops.apply_guild_troop_losses(
            guild=defender_locked,
            loadout=defender_setup.get("troop_loadout", {}),
            report=report,
            side="defender",
        )

    is_attacker_victory = getattr(report, "winner", "") == "attacker"
    loot_silver = 0
    loot_items: dict[str, int] = {}
    if is_attacker_victory:
        loot_silver, loot_items = transfer_guild_raid_loot(
            attacker_guild=locked_run.attacker_guild,
            defender_guild=defender_locked,
        )
        _apply_guild_defeat_protection(defender_locked, now=processed_at)

    exp_fruit_count, equipment_recovery = calculate_battle_salvage(report)
    reward_items = dict(equipment_recovery or {})
    if int(exp_fruit_count or 0) > 0:
        reward_items["experience_fruit"] = int(exp_fruit_count)
    winner_guild = locked_run.attacker_guild if is_attacker_victory else defender_locked
    battle_rewards = grant_guild_raid_battle_rewards(guild=winner_guild, rewards=reward_items)

    locked_run.status = GuildRaidRun.Status.RETURNING
    locked_run.battle_report = report
    locked_run.battle_rewards = battle_rewards
    locked_run.loot_silver = loot_silver
    locked_run.loot_items = loot_items
    locked_run.is_attacker_victory = is_attacker_victory
    locked_run.blocked_reason = ""
    locked_run.battle_at = locked_run.battle_at or processed_at
    locked_run.save(
        update_fields=[
            "status",
            "battle_report",
            "battle_rewards",
            "loot_silver",
            "loot_items",
            "is_attacker_victory",
            "blocked_reason",
            "battle_at",
        ]
    )
    schedule_guild_raid_completion(locked_run)
    send_guild_raid_report_messages(locked_run, report)
    return True


@transaction.atomic
def finalize_guild_raid(run: GuildRaidRun, *, now=None) -> bool:
    finalized_at = now or timezone.now()
    locked_run = (
        GuildRaidRun.objects.select_for_update()
        .select_related(
            "attacker_guild",
            "attacker_guild__founder",
            "defender_guild",
            "defender_guild__founder",
            "started_by__user",
            "battle_report",
        )
        .filter(pk=run.pk)
        .first()
    )
    if locked_run is None or locked_run.status not in (GuildRaidRun.Status.RETURNING, GuildRaidRun.Status.RETREATED):
        return False
    if locked_run.return_at is not None and locked_run.return_at > finalized_at:
        return False

    if locked_run.status == GuildRaidRun.Status.RETREATED:
        surviving_troops = dict(locked_run.troop_loadout or {})
    else:
        surviving_troops = guild_troops.calculate_surviving_guild_troops(
            locked_run.troop_loadout,
            locked_run.battle_report,
        )
    guild_troops.add_guild_troops(guild=locked_run.attacker_guild, loadout=surviving_troops)

    locked_run.status = GuildRaidRun.Status.COMPLETED
    locked_run.completed_at = finalized_at
    locked_run.save(update_fields=["status", "completed_at"])
    return True


def process_due_guild_raid(run: GuildRaidRun, *, now=None) -> bool:
    processed_at = now or timezone.now()
    if run.status == GuildRaidRun.Status.MARCHING:
        return process_guild_raid_battle(run, now=processed_at)
    if run.status in (GuildRaidRun.Status.RETURNING, GuildRaidRun.Status.RETREATED):
        return finalize_guild_raid(run, now=processed_at)
    return False
