from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from battle.execution import BattleOptions, execute_battle
from common.utils.celery import safe_apply_async
from core.exceptions import GuildValidationError
from gameplay.models import Manor
from gameplay.services.battle_salvage import calculate_battle_salvage
from gameplay.services.battle_snapshots import build_guest_battle_snapshots, build_guest_snapshot_proxies
from gameplay.services.manor.core import ensure_manor

from .. import constants as guild_constants
from ..models import Guild, GuildRaidRun
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
from .guild_raid_support import apply_guild_defeat_protection as _apply_guild_defeat_protection
from .guild_raid_support import dispatch_countdown_for_run as _dispatch_countdown_for_run
from .guild_raid_support import load_defender_guests as _load_defender_guests
from .guild_raid_support import lock_guild_pair as _lock_guild_pair
from .technology import build_guild_troop_tech_levels, get_guild_dispatch_capacity

logger = logging.getLogger(__name__)
IN_FLIGHT_GUILD_RAID_STATUSES = (
    GuildRaidRun.Status.MARCHING,
    GuildRaidRun.Status.BATTLING,
    GuildRaidRun.Status.RETURNING,
    GuildRaidRun.Status.RETREATED,
)


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


def process_due_guild_pvp_activity(guild: Guild, *, now=None, include_incoming_marching: bool = True) -> int:
    return refresh_due_guild_raids(
        guild,
        now=now,
        include_incoming_marching=include_incoming_marching,
    )


def prepare_guild_pvp_read_state(guild: Guild, *, now=None) -> None:
    del guild, now


def _schedule_guild_raid_warning_messages(run: GuildRaidRun) -> None:
    def _send_warning_messages_after_commit() -> None:
        send_guild_raid_warning_messages(run)

    transaction.on_commit(_send_warning_messages_after_commit)


def _schedule_guild_raid_report_messages(run: GuildRaidRun, report: Any) -> None:
    def _send_report_messages_after_commit() -> None:
        send_guild_raid_report_messages(run, report)

    transaction.on_commit(_send_report_messages_after_commit)


def start_guild_raid(
    *,
    guild: Guild,
    defender_guild: Guild,
    operator,
    pool_entry_ids: list[int],
    troop_loadout: dict[str, int],
) -> GuildRaidRun:
    if guild.pk == defender_guild.pk:
        raise GuildValidationError("不能进攻自己的帮会")

    refresh_due_guild_raids(guild)
    return _start_guild_raid_atomic(
        guild=guild,
        defender_guild=defender_guild,
        operator=operator,
        pool_entry_ids=pool_entry_ids,
        troop_loadout=troop_loadout,
    )


@transaction.atomic
def _start_guild_raid_atomic(
    *,
    guild: Guild,
    defender_guild: Guild,
    operator,
    pool_entry_ids: list[int],
    troop_loadout: dict[str, int],
) -> GuildRaidRun:
    locked_guild, locked_defender = _lock_guild_pair(
        attacker_guild_id=guild.pk,
        defender_guild_id=defender_guild.pk,
        require_active=True,
    )

    today = timezone.localdate()
    reset_guild_pvp_counters_if_needed(locked_guild, today=today)
    reset_guild_pvp_counters_if_needed(locked_defender, today=today)
    can_attack, blocked_reason = can_attack_guild(attacker_guild=locked_guild, defender_guild=locked_defender)
    if not can_attack:
        raise GuildValidationError(blocked_reason or "当前不可发起帮会攻击")

    membership = lock_manage_member(guild=locked_guild, operator=operator, permission_label="发起帮会攻击")

    if (
        GuildRaidRun.objects.select_for_update()
        .filter(
            attacker_guild=locked_guild,
            status__in=IN_FLIGHT_GUILD_RAID_STATUSES,
        )
        .exists()
    ):
        raise GuildValidationError("当前已有帮会对战队伍出征中")

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
        attacker_troop_tech_snapshot=attacker_troop_tech_snapshot,
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
    _schedule_guild_raid_warning_messages(run)
    return run


def _lock_guild_roots_for_raid_run(run_id: int) -> tuple[Guild, Guild] | None:
    guild_ids = GuildRaidRun.objects.filter(pk=run_id).values_list("attacker_guild_id", "defender_guild_id").first()
    if guild_ids is None:
        return None
    return _lock_guild_pair(attacker_guild_id=guild_ids[0], defender_guild_id=guild_ids[1])


def _report_owner_user_id_for_raid_run(run_id: int) -> int | None:
    owner_ids = (
        GuildRaidRun.objects.filter(pk=run_id).values_list("started_by__user_id", "attacker_guild__founder_id").first()
    )
    if owner_ids is None:
        return None
    return owner_ids[0] or owner_ids[1]


def _ensure_report_owner_for_raid_run(run_id: int) -> int | None:
    owner_user_id = _report_owner_user_id_for_raid_run(run_id)
    if owner_user_id is None:
        logger.error("Guild raid report owner is unavailable: run_id=%s", run_id)
        return None
    if Manor.objects.filter(user_id=owner_user_id).exists():
        return owner_user_id

    user = get_user_model().objects.filter(pk=owner_user_id).first()
    if user is None:
        logger.error(
            "Guild raid report owner user is missing: run_id=%s user_id=%s",
            run_id,
            owner_user_id,
        )
        return None
    ensure_manor(user)
    return owner_user_id


def _lock_report_owner_manor_for_raid_run(run_id: int, *, owner_user_id: int) -> Manor | None:
    return Manor.objects.select_for_update().filter(user_id=owner_user_id).first()


def _locked_raid_report_owner_user_id(run: GuildRaidRun) -> int | None:
    started_by = run.started_by
    if started_by is not None:
        return started_by.user_id
    return run.attacker_guild.founder_id


def request_retreat(*, run: GuildRaidRun, operator) -> None:
    now = timezone.now()
    overdue_guild: Guild | None = None

    with transaction.atomic():
        locked_guilds = _lock_guild_roots_for_raid_run(run.pk)
        if locked_guilds is None:
            raise GuildValidationError("帮会出征不存在")
        attacker_locked, _defender_locked = locked_guilds
        lock_manage_member(guild=attacker_locked, operator=operator, permission_label="撤回帮会攻击")
        locked_run = GuildRaidRun.objects.select_for_update().select_related("attacker_guild").filter(pk=run.pk).first()
        if locked_run is None:
            raise GuildValidationError("帮会出征不存在")

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


def process_guild_raid_battle(run: GuildRaidRun, *, now=None) -> bool:
    owner_user_id = _ensure_report_owner_for_raid_run(run.pk)
    if owner_user_id is None:
        return False
    return _process_guild_raid_battle_atomic(run, now=now, expected_report_owner_user_id=owner_user_id)


@transaction.atomic
def _process_guild_raid_battle_atomic(
    run: GuildRaidRun,
    *,
    now=None,
    expected_report_owner_user_id: int,
) -> bool:
    processed_at = now or timezone.now()
    report_owner = _lock_report_owner_manor_for_raid_run(
        run.pk,
        owner_user_id=expected_report_owner_user_id,
    )
    if report_owner is None:
        return False
    locked_guilds = _lock_guild_roots_for_raid_run(run.pk)
    if locked_guilds is None:
        return False
    attacker_locked, defender_locked = locked_guilds
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
    if _locked_raid_report_owner_user_id(locked_run) != expected_report_owner_user_id:
        logger.info(
            "Guild raid report owner changed during settlement; retrying from fresh state: run_id=%s",
            run.pk,
        )
        return False

    locked_run.attacker_guild = attacker_locked
    locked_run.defender_guild = defender_locked
    blocked_reason = get_guild_battle_block_reason(defender_guild=defender_locked, now=processed_at)
    if blocked_reason:
        locked_run.status = GuildRaidRun.Status.RETREATED
        locked_run.blocked_reason = blocked_reason
        locked_run.save(update_fields=["status", "blocked_reason"])
        schedule_guild_raid_completion(locked_run)
        return True

    locked_run.status = GuildRaidRun.Status.BATTLING
    locked_run.save(update_fields=["status"])

    guest_models = build_guest_snapshot_proxies(locked_run.guest_snapshots, include_guest_identity=True)
    battle_guest_models = cast(list[Any], guest_models)
    attacker_limit = max(1, int(getattr(locked_run, "selected_guest_count", 0) or len(battle_guest_models)))
    defender_guests = _load_defender_guests(defender_locked)
    defender_setup = guild_troops.build_guild_defender_setup(guild=defender_locked)
    attacker_tech_levels = dict(locked_run.attacker_troop_tech_snapshot or {})
    if not attacker_tech_levels:
        attacker_tech_levels = build_guild_troop_tech_levels(attacker_locked)
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
            attacker_tech_levels=attacker_tech_levels,
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
            attacker_guild=attacker_locked,
            defender_guild=defender_locked,
            guests=battle_guest_models,
            troop_loadout=locked_run.troop_loadout,
            battle_report=report,
        )
        _apply_guild_defeat_protection(defender_locked, now=processed_at)

    exp_fruit_count, equipment_recovery = calculate_battle_salvage(report)
    reward_items = dict(equipment_recovery or {})
    if int(exp_fruit_count or 0) > 0:
        reward_items["experience_fruit"] = int(exp_fruit_count)
    winner_guild = attacker_locked if is_attacker_victory else defender_locked
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
    _schedule_guild_raid_report_messages(locked_run, report)
    return True


@transaction.atomic
def finalize_guild_raid(run: GuildRaidRun, *, now=None) -> bool:
    finalized_at = now or timezone.now()
    locked_guilds = _lock_guild_roots_for_raid_run(run.pk)
    if locked_guilds is None:
        return False
    attacker_locked, defender_locked = locked_guilds
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
    locked_run.attacker_guild = attacker_locked
    locked_run.defender_guild = defender_locked
    guild_troops.add_guild_troops(guild=attacker_locked, loadout=surviving_troops)

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
