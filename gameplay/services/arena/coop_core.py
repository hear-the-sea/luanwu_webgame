from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from battle.arena_coop import ARENA_COOP_ENEMY_FINAL_STATS, configure_arena_coop_enemy_guest
from battle.combatants_pkg import build_named_ai_guests
from battle.models import BattleReport
from battle.services import simulate_report
from core.exceptions import ArenaBusyError, ArenaCancellationError, ArenaEntryStateError, ArenaParticipationLimitError
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock
from gameplay.models import (
    ArenaCoopContribution,
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ItemTemplate,
    Manor,
    Message,
    ResourceEvent,
)
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.utils.messages import create_message
from guests.models import Guest, GuestStatus

from . import helpers as _arena_helpers
from .coop_damage import aggregate_event_damage
from .coop_rewards import build_reward_breakdown, rank_contribution_rows
from .coop_rules import load_arena_coop_rules
from .snapshots import build_entry_guest_snapshot, load_entry_guests

logger = logging.getLogger(__name__)

_load_positive_int_setting = _arena_helpers.load_positive_int_setting
_normalize_guest_ids = _arena_helpers.normalize_guest_ids
_today_bounds = _arena_helpers.today_bounds
_today_local_date = _arena_helpers.today_local_date


ARENA_COOP_RULES = load_arena_coop_rules()
ARENA_COOP_PLAYER_LIMIT = _load_positive_int_setting(
    "ARENA_COOP_PLAYER_LIMIT",
    ARENA_COOP_RULES["registration"]["player_limit"],
    minimum=2,
)
ARENA_COOP_MAX_GUESTS_PER_ENTRY = _load_positive_int_setting(
    "ARENA_COOP_MAX_GUESTS_PER_ENTRY",
    ARENA_COOP_RULES["registration"]["guest_limit_per_entry"],
    minimum=1,
)
ARENA_COOP_DAILY_PARTICIPATION_LIMIT = _load_positive_int_setting(
    "ARENA_COOP_DAILY_PARTICIPATION_LIMIT",
    ARENA_COOP_RULES["registration"]["daily_participation_limit"],
    minimum=1,
)
ARENA_COOP_PREPARE_DURATION_SECONDS = _load_positive_int_setting(
    "ARENA_COOP_PREPARE_DURATION_SECONDS",
    ARENA_COOP_RULES["registration"]["prepare_duration_seconds"],
    minimum=1,
)
ARENA_COOP_COMPLETED_RETENTION_SECONDS = int(ARENA_COOP_RULES["runtime"]["completed_retention_seconds"])
ARENA_COOP_MINIMUM_SHARE_BPS = int(ARENA_COOP_RULES["contribution"]["minimum_share_bps"])
ARENA_COOP_REGISTRATION_SILVER_COST = int(ARENA_COOP_RULES["registration"]["registration_silver_cost"])
ARENA_COOP_RECRUITING_LOCK_KEY = str(ARENA_COOP_RULES["registration"]["recruiting_lock_key"])
ARENA_COOP_RECRUITING_LOCK_TIMEOUT = _load_positive_int_setting(
    "ARENA_COOP_RECRUITING_LOCK_TIMEOUT",
    ARENA_COOP_RULES["registration"]["recruiting_lock_timeout"],
    minimum=1,
)


def refresh_arena_coop_constants() -> None:
    global ARENA_COOP_RULES
    global ARENA_COOP_PLAYER_LIMIT
    global ARENA_COOP_MAX_GUESTS_PER_ENTRY
    global ARENA_COOP_DAILY_PARTICIPATION_LIMIT
    global ARENA_COOP_PREPARE_DURATION_SECONDS
    global ARENA_COOP_COMPLETED_RETENTION_SECONDS
    global ARENA_COOP_MINIMUM_SHARE_BPS
    global ARENA_COOP_REGISTRATION_SILVER_COST
    global ARENA_COOP_RECRUITING_LOCK_KEY
    global ARENA_COOP_RECRUITING_LOCK_TIMEOUT

    ARENA_COOP_RULES = load_arena_coop_rules()
    ARENA_COOP_PLAYER_LIMIT = _load_positive_int_setting(
        "ARENA_COOP_PLAYER_LIMIT",
        ARENA_COOP_RULES["registration"]["player_limit"],
        minimum=2,
    )
    ARENA_COOP_MAX_GUESTS_PER_ENTRY = _load_positive_int_setting(
        "ARENA_COOP_MAX_GUESTS_PER_ENTRY",
        ARENA_COOP_RULES["registration"]["guest_limit_per_entry"],
        minimum=1,
    )
    ARENA_COOP_DAILY_PARTICIPATION_LIMIT = _load_positive_int_setting(
        "ARENA_COOP_DAILY_PARTICIPATION_LIMIT",
        ARENA_COOP_RULES["registration"]["daily_participation_limit"],
        minimum=1,
    )
    ARENA_COOP_PREPARE_DURATION_SECONDS = _load_positive_int_setting(
        "ARENA_COOP_PREPARE_DURATION_SECONDS",
        ARENA_COOP_RULES["registration"]["prepare_duration_seconds"],
        minimum=1,
    )
    ARENA_COOP_COMPLETED_RETENTION_SECONDS = int(ARENA_COOP_RULES["runtime"]["completed_retention_seconds"])
    ARENA_COOP_MINIMUM_SHARE_BPS = int(ARENA_COOP_RULES["contribution"]["minimum_share_bps"])
    ARENA_COOP_REGISTRATION_SILVER_COST = int(ARENA_COOP_RULES["registration"]["registration_silver_cost"])
    ARENA_COOP_RECRUITING_LOCK_KEY = str(ARENA_COOP_RULES["registration"]["recruiting_lock_key"])
    ARENA_COOP_RECRUITING_LOCK_TIMEOUT = _load_positive_int_setting(
        "ARENA_COOP_RECRUITING_LOCK_TIMEOUT",
        ARENA_COOP_RULES["registration"]["recruiting_lock_timeout"],
        minimum=1,
    )


@dataclass(frozen=True)
class ArenaCoopRegistrationResult:
    entry: ArenaCoopEntry
    event: ArenaCoopEvent
    moved_to_preparing: bool
    entry_count: int


def _merge_mapping(target: dict, updates: dict | None) -> dict:
    if not isinstance(updates, dict):
        return target
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_mapping(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _load_runtime_rules_for_event(event: ArenaCoopEvent) -> dict:
    rules = copy.deepcopy(ARENA_COOP_RULES)
    _merge_mapping(rules["enemy"], event.enemy_snapshot if isinstance(event.enemy_snapshot, dict) else {})
    reward_snapshot = event.reward_snapshot if isinstance(event.reward_snapshot, dict) else {}
    _merge_mapping(rules["rewards"], reward_snapshot.get("rewards"))
    _merge_mapping(rules["rare_drop"], reward_snapshot.get("rare_drop"))
    daily_snapshot = event.daily_rule_snapshot if isinstance(event.daily_rule_snapshot, dict) else {}
    _merge_mapping(rules["registration"], daily_snapshot.get("registration"))
    _merge_mapping(rules["contribution"], daily_snapshot.get("contribution"))
    return rules


def _resolve_boss_initial_hp(boss_template_key: str) -> int:
    profile = ARENA_COOP_ENEMY_FINAL_STATS.get(str(boss_template_key or "").strip(), {})
    return max(0, int(profile.get("final_hp", 0) or 0))


def _extract_boss_hp_snapshot(report: BattleReport, *, boss_template_key: str) -> tuple[int, int] | None:
    for member in report.defender_team or []:
        if not isinstance(member, dict):
            continue
        template_key = str(member.get("template_key") or "").strip()
        is_boss = bool(member.get("is_boss"))
        if template_key != boss_template_key and not is_boss:
            continue
        initial_hp = max(0, int(member.get("initial_hp") or 0))
        remaining_hp = max(0, int(member.get("remaining_hp") or 0))
        return initial_hp, remaining_hp
    return None


def _apply_combatant_metadata(guest, *, owner_entry_id: int | None, combatant_slot: int, is_boss: bool) -> None:
    setattr(guest, "_owner_entry_id", owner_entry_id)
    setattr(guest, "_combatant_slot", combatant_slot)
    setattr(guest, "_is_boss", is_boss)


def _build_attacker_guest_pool(registered_entries: list[ArenaCoopEntry], *, guest_limit_per_entry: int) -> list:
    attacker_guests: list = []
    for entry in registered_entries:
        for slot_index, guest in enumerate(load_entry_guests(entry, max_guests_per_entry=guest_limit_per_entry)):
            _apply_combatant_metadata(guest, owner_entry_id=entry.id, combatant_slot=slot_index, is_boss=False)
            attacker_guests.append(guest)
    return attacker_guests


def _build_defender_guest_pool(locked_event: ArenaCoopEvent) -> list[Guest]:
    enemy_snapshot = locked_event.enemy_snapshot if isinstance(locked_event.enemy_snapshot, dict) else {}
    raw_boss = enemy_snapshot.get("boss")
    boss: dict[str, object] = raw_boss if isinstance(raw_boss, dict) else {}
    raw_guards = enemy_snapshot.get("guards")
    guards: list[object] = raw_guards if isinstance(raw_guards, list) else []

    defender_guest_keys: list[dict[str, str]] = []
    boss_template_key = str(boss.get("template_key") or locked_event.boss_template_key)
    defender_guest_keys.append(
        {
            "key": boss_template_key,
            "label": str(boss.get("display_name") or locked_event.boss_name),
        }
    )
    for guard in guards:
        if not isinstance(guard, dict):
            continue
        template_key = str(guard.get("template_key") or "").strip()
        if not template_key:
            continue
        defender_guest_keys.append(
            {
                "key": template_key,
                "label": str(guard.get("display_name") or template_key),
            }
        )

    defender_guests = build_named_ai_guests(defender_guest_keys, level=90)
    for slot_index, guest in enumerate(defender_guests):
        configure_arena_coop_enemy_guest(guest)
        is_boss = slot_index == 0
        _apply_combatant_metadata(guest, owner_entry_id=None, combatant_slot=slot_index, is_boss=is_boss)
    return defender_guests


def _run_coop_battle_locked(locked_event: ArenaCoopEvent, now: datetime) -> BattleReport:
    registered_entries = list(
        locked_event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
        .select_related("manor")
        .order_by("joined_at", "id")
    )
    attacker_guests = _build_attacker_guest_pool(
        registered_entries,
        guest_limit_per_entry=locked_event.guest_limit_per_entry,
    )
    defender_guests = _build_defender_guest_pool(locked_event)
    report_manor = registered_entries[0].manor
    return simulate_report(
        report_manor,
        battle_type="arena_coop",
        troop_loadout={},
        fill_default_troops=False,
        attacker_guests=attacker_guests,
        defender_guests=defender_guests,
        max_squad=max(1, len(attacker_guests)),
        defender_max_squad=max(1, len(defender_guests)),
        auto_reward=False,
        send_message=False,
        apply_damage=False,
        use_lock=False,
        opponent_name=locked_event.boss_name,
    )


def _grant_coop_reward_locked(locked_manor: Manor, *, total_coins: int, rare_drop_item_key: str) -> None:
    if total_coins > 0:
        locked_manor.arena_coins = F("arena_coins") + int(total_coins)
        locked_manor.save(update_fields=["arena_coins"])

    rare_drop_key = str(rare_drop_item_key or "").strip()
    if rare_drop_key and ItemTemplate.objects.filter(key=rare_drop_key).exists():
        add_item_to_inventory_locked(locked_manor, rare_drop_key, 1)


def _format_rare_drop_summary(rare_drop_item_key: str) -> str:
    item_key = str(rare_drop_item_key or "").strip()
    if not item_key:
        return "未掉落稀有奖励"
    item_name = ItemTemplate.objects.filter(key=item_key).values_list("name", flat=True).first() or item_key
    return f"掉落稀有奖励：{item_name}"


def _send_coop_settlement_messages(
    *,
    locked_event: ArenaCoopEvent,
    locked_manor: Manor,
    contribution: ArenaCoopContribution,
    report: BattleReport,
) -> None:
    battle_title = "围攻光明顶战报"
    battle_body = f"围攻光明顶已结算，本场敌首为{locked_event.boss_name}，请查收战报。"
    reward_title = "围攻光明顶结算"
    reward_body = (
        f"总伤害 {contribution.total_damage}，"
        f"Boss伤害 {contribution.boss_damage}，"
        f"排名第 {contribution.damage_rank}，"
        f"角斗币 {contribution.total_coins}。"
        f"{_format_rare_drop_summary(contribution.rare_drop_item_key)}。"
    )

    try:
        create_message(
            manor=locked_manor,
            kind=Message.Kind.BATTLE,
            title=battle_title,
            body=battle_body,
            battle_report=report,
        )
        create_message(
            manor=locked_manor,
            kind=Message.Kind.REWARD,
            title=reward_title,
            body=reward_body,
        )
    except Exception:
        logger.exception(
            "arena coop settlement messages failed: event_id=%s manor_id=%s entry_id=%s",
            locked_event.id,
            locked_manor.id,
            contribution.entry_id,
        )


def _settle_coop_event_locked(
    locked_event: ArenaCoopEvent,
    report: BattleReport,
    registered_entries: list[ArenaCoopEntry],
    now: datetime,
) -> None:
    rules = _load_runtime_rules_for_event(locked_event)
    damage_rows = aggregate_event_damage(report.rounds or [], boss_template_key=locked_event.boss_template_key)
    total_damage = sum(row["total_damage"] for row in damage_rows.values()) or 1
    ranked_rows: list[dict] = []

    for entry in registered_entries:
        bucket = damage_rows.get(entry.id, {"total_damage": 0, "boss_damage": 0, "guard_damage": 0})
        share_bps = int(bucket["total_damage"] * 10000 / total_damage)
        ranked_rows.append(
            {
                "entry_id": entry.id,
                "joined_at": entry.joined_at,
                "effective_damage": bucket["total_damage"],
                "total_damage": bucket["total_damage"],
                "boss_damage": bucket["boss_damage"],
                "guard_damage": bucket["guard_damage"],
                "damage_share_bps": share_bps,
                "met_minimum_contribution": share_bps >= int(rules["contribution"]["minimum_share_bps"]),
            }
        )

    boss_defeated = report.winner == "attacker"
    ranked_rows = rank_contribution_rows(ranked_rows)
    ranked_entry_ids = [int(row["entry_id"]) for row in ranked_rows]
    entry_map = {entry.id: entry for entry in registered_entries}

    ArenaCoopContribution.objects.filter(event=locked_event).delete()
    contribution_rows: list[ArenaCoopContribution] = []
    for rank, row in enumerate(ranked_rows, start=1):
        row["damage_rank"] = rank
        reward_breakdown = build_reward_breakdown(row, rules=rules, boss_defeated=boss_defeated)
        entry = entry_map[row["entry_id"]]
        contribution_rows.append(
            ArenaCoopContribution(
                event=locked_event,
                entry=entry,
                total_damage=row["total_damage"],
                boss_damage=row["boss_damage"],
                guard_damage=row["guard_damage"],
                effective_damage=row["effective_damage"],
                damage_share_bps=row["damage_share_bps"],
                damage_rank=rank,
                met_minimum_contribution=row["met_minimum_contribution"],
                participation_coins=reward_breakdown["participation_coins"],
                damage_coins=reward_breakdown["damage_coins"],
                rank_coins=reward_breakdown["rank_coins"],
                clear_coins=reward_breakdown["clear_coins"],
                total_coins=reward_breakdown["total_coins"],
                rare_drop_item_key=reward_breakdown["rare_drop_item_key"],
                rare_drop_quantity=1 if reward_breakdown["rare_drop_granted"] else 0,
                rare_drop_granted=reward_breakdown["rare_drop_granted"],
                reward_payload=reward_breakdown,
            )
        )
    ArenaCoopContribution.objects.bulk_create(contribution_rows)

    locked_entries = list(
        ArenaCoopEntry.objects.select_for_update()
        .filter(id__in=ranked_entry_ids)
        .select_related("manor")
        .order_by("id")
    )
    contribution_map = {row.entry_id: row for row in contribution_rows}
    for entry in locked_entries:
        entry.status = ArenaCoopEntry.Status.COMPLETED
    ArenaCoopEntry.objects.bulk_update(locked_entries, ["status"])

    for entry in locked_entries:
        locked_manor = Manor.objects.select_for_update().get(pk=entry.manor_id)
        contribution = contribution_map[entry.id]
        _grant_coop_reward_locked(
            locked_manor,
            total_coins=contribution.total_coins,
            rare_drop_item_key=contribution.rare_drop_item_key,
        )
        _send_coop_settlement_messages(
            locked_event=locked_event,
            locked_manor=locked_manor,
            contribution=contribution,
            report=report,
        )

    locked_event.status = ArenaCoopEvent.Status.COMPLETED
    locked_event.battle_report = report
    locked_event.boss_defeated = boss_defeated
    locked_event.started_at = locked_event.started_at or now
    locked_event.ended_at = now
    boss_hp_snapshot = _extract_boss_hp_snapshot(report, boss_template_key=locked_event.boss_template_key)
    if boss_hp_snapshot is not None:
        locked_event.boss_initial_hp, locked_event.boss_remaining_hp = boss_hp_snapshot
    elif boss_defeated:
        locked_event.boss_remaining_hp = 0
    locked_event.save(
        update_fields=[
            "status",
            "battle_report",
            "boss_defeated",
            "started_at",
            "ended_at",
            "boss_initial_hp",
            "boss_remaining_hp",
            "updated_at",
        ]
    )


def _load_selected_guests_locked(locked_manor: Manor, selected_guest_ids: Iterable[int]) -> list[Guest]:
    requested_guest_ids = [int(guest_id) for guest_id in selected_guest_ids]
    all_selected_guests = list(
        Guest.objects.select_for_update()
        .filter(manor=locked_manor, id__in=requested_guest_ids)
        .select_related("template")
        .prefetch_related("skills")
        .order_by("id")
    )
    if len(all_selected_guests) != len(requested_guest_ids):
        raise ArenaEntryStateError("所选门客不存在或不属于当前庄园")

    non_idle_guests = [guest for guest in all_selected_guests if guest.status != GuestStatus.IDLE]
    if non_idle_guests:
        raise ArenaEntryStateError("仅空闲门客可报名围攻光明顶")

    selected_guest_order = {guest_id: index for index, guest_id in enumerate(requested_guest_ids)}
    return sorted(all_selected_guests, key=lambda guest: selected_guest_order[guest.id])


def _deduct_registration_silver_locked(locked_manor: Manor, *, silver_cost: int) -> None:
    if silver_cost <= 0:
        return

    from gameplay.services.resources import spend_resources_locked

    spend_resources_locked(
        locked_manor,
        {"silver": silver_cost},
        note="竞技场共斗报名",
        reason=ResourceEvent.Reason.UPGRADE_COST,
    )


def _sync_daily_counter_locked(locked_manor: Manor, *, now=None) -> int:
    today = _today_local_date(now=now)
    if locked_manor.arena_coop_participation_date == today:
        return max(0, int(locked_manor.arena_coop_participations_today or 0))

    day_start, day_end = _today_bounds(now=now)
    today_count = (
        ArenaCoopEntry.objects.filter(
            manor=locked_manor,
            joined_at__gte=day_start,
            joined_at__lt=day_end,
        )
        .exclude(status=ArenaCoopEntry.Status.CANCELLED)
        .count()
    )
    locked_manor.arena_coop_participation_date = today
    locked_manor.arena_coop_participations_today = max(0, int(today_count))
    locked_manor.save(update_fields=["arena_coop_participation_date", "arena_coop_participations_today"])
    return locked_manor.arena_coop_participations_today


def _update_daily_counter_locked(locked_manor: Manor, *, delta: int, now=None) -> int:
    current = _sync_daily_counter_locked(locked_manor, now=now)
    updated = max(0, int(current) + int(delta))
    locked_manor.arena_coop_participation_date = _today_local_date(now=now)
    locked_manor.arena_coop_participations_today = updated
    locked_manor.save(update_fields=["arena_coop_participation_date", "arena_coop_participations_today"])
    return updated


def _build_event_snapshots() -> tuple[dict, dict, dict]:
    return (
        copy.deepcopy(ARENA_COOP_RULES["enemy"]),
        {
            "rewards": copy.deepcopy(ARENA_COOP_RULES["rewards"]),
            "rare_drop": copy.deepcopy(ARENA_COOP_RULES["rare_drop"]),
        },
        {
            "registration": copy.deepcopy(ARENA_COOP_RULES["registration"]),
            "contribution": copy.deepcopy(ARENA_COOP_RULES["contribution"]),
        },
    )


def _get_or_create_recruiting_event_locked() -> ArenaCoopEvent:
    event = (
        ArenaCoopEvent.objects.select_for_update()
        .filter(status=ArenaCoopEvent.Status.RECRUITING)
        .annotate(
            registered_entry_count=Count(
                "entries",
                filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
            )
        )
        .filter(registered_entry_count__lt=F("player_limit"))
        .order_by("created_at")
        .first()
    )
    if event:
        return event

    acquired, from_cache, lock_token = acquire_best_effort_lock(
        ARENA_COOP_RECRUITING_LOCK_KEY,
        timeout_seconds=ARENA_COOP_RECRUITING_LOCK_TIMEOUT,
        logger=logger,
        log_context="arena coop recruiting event lock",
        allow_local_fallback=False,
    )
    if not acquired:
        existing = (
            ArenaCoopEvent.objects.filter(status=ArenaCoopEvent.Status.RECRUITING)
            .annotate(
                registered_entry_count=Count(
                    "entries",
                    filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
                )
            )
            .filter(registered_entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )
        if existing:
            return existing
        raise ArenaBusyError()

    try:
        existing = (
            ArenaCoopEvent.objects.select_for_update()
            .filter(status=ArenaCoopEvent.Status.RECRUITING)
            .annotate(
                registered_entry_count=Count(
                    "entries",
                    filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
                )
            )
            .filter(registered_entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )
        if existing:
            return existing

        enemy_snapshot, reward_snapshot, daily_rule_snapshot = _build_event_snapshots()
        return ArenaCoopEvent.objects.create(
            status=ArenaCoopEvent.Status.RECRUITING,
            player_limit=ARENA_COOP_PLAYER_LIMIT,
            guest_limit_per_entry=ARENA_COOP_MAX_GUESTS_PER_ENTRY,
            prepare_duration_seconds=ARENA_COOP_PREPARE_DURATION_SECONDS,
            boss_name=str(ARENA_COOP_RULES["enemy"]["boss"]["display_name"]),
            boss_template_key=str(ARENA_COOP_RULES["enemy"]["boss"]["template_key"]),
            boss_initial_hp=_resolve_boss_initial_hp(ARENA_COOP_RULES["enemy"]["boss"]["template_key"]),
            boss_remaining_hp=_resolve_boss_initial_hp(ARENA_COOP_RULES["enemy"]["boss"]["template_key"]),
            enemy_snapshot=enemy_snapshot,
            reward_snapshot=reward_snapshot,
            daily_rule_snapshot=daily_rule_snapshot,
        )
    finally:
        release_best_effort_lock(
            ARENA_COOP_RECRUITING_LOCK_KEY,
            from_cache=from_cache,
            lock_token=lock_token,
            logger=logger,
            log_context="arena coop recruiting event lock",
        )


def _move_event_to_preparing_locked(event: ArenaCoopEvent, *, now: datetime | None = None) -> bool:
    if event.status != ArenaCoopEvent.Status.RECRUITING:
        return False

    registered_count = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count()
    if registered_count < event.player_limit:
        return False

    current_time = now or timezone.now()
    event.status = ArenaCoopEvent.Status.PREPARING
    event.prepare_ends_at = current_time + timedelta(seconds=event.prepare_duration_seconds)
    event.save(update_fields=["status", "prepare_ends_at", "updated_at"])
    return True


def _create_entry_with_snapshots_locked(
    event: ArenaCoopEvent,
    locked_manor: Manor,
    selected_guests: list[Guest],
) -> ArenaCoopEntry:
    entry = ArenaCoopEntry.objects.create(event=event, manor=locked_manor)
    ArenaCoopEntryGuest.objects.bulk_create(
        [
            ArenaCoopEntryGuest(
                entry=entry,
                guest=guest,
                slot_index=index,
                snapshot=build_entry_guest_snapshot(guest),
            )
            for index, guest in enumerate(selected_guests)
        ]
    )
    for guest in selected_guests:
        guest.status = GuestStatus.ARENA
    Guest.objects.bulk_update(selected_guests, ["status"])
    return entry


def _release_entry_guest_statuses(entry: ArenaCoopEntry) -> None:
    guest_ids = list(entry.entry_guests.values_list("guest_id", flat=True))
    if not guest_ids:
        return
    Guest.objects.filter(
        id__in=guest_ids,
        status__in=[GuestStatus.ARENA, GuestStatus.DEPLOYED],
    ).update(status=GuestStatus.IDLE)


@transaction.atomic
def register_arena_coop_entry(manor: Manor, guest_ids: Iterable[int]) -> ArenaCoopRegistrationResult:
    selected_guest_ids = _normalize_guest_ids(guest_ids, max_guests_per_entry=ARENA_COOP_MAX_GUESTS_PER_ENTRY)
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    if _sync_daily_counter_locked(locked_manor) >= ARENA_COOP_DAILY_PARTICIPATION_LIMIT:
        raise ArenaParticipationLimitError(
            ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
            message=f"每日最多参加 {ARENA_COOP_DAILY_PARTICIPATION_LIMIT} 次围攻光明顶",
        )

    if ArenaCoopEntry.objects.filter(
        manor=locked_manor,
        status=ArenaCoopEntry.Status.REGISTERED,
        event__status__in=[
            ArenaCoopEvent.Status.RECRUITING,
            ArenaCoopEvent.Status.PREPARING,
            ArenaCoopEvent.Status.RUNNING,
        ],
    ).exists():
        raise ArenaEntryStateError("您已有进行中的围攻光明顶报名，请等待本场结束")

    selected_guests = _load_selected_guests_locked(locked_manor, selected_guest_ids)
    _deduct_registration_silver_locked(locked_manor, silver_cost=ARENA_COOP_REGISTRATION_SILVER_COST)
    event = _get_or_create_recruiting_event_locked()
    entry = _create_entry_with_snapshots_locked(event, locked_manor, selected_guests)
    entry_count = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count()
    moved_to_preparing = False
    if entry_count >= event.player_limit:
        moved_to_preparing = _move_event_to_preparing_locked(event)
    _update_daily_counter_locked(locked_manor, delta=1)
    return ArenaCoopRegistrationResult(
        entry=entry,
        event=event,
        moved_to_preparing=moved_to_preparing,
        entry_count=entry_count,
    )


@transaction.atomic
def cancel_arena_coop_entry(manor: Manor) -> int:
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    entry = (
        ArenaCoopEntry.objects.select_for_update()
        .select_related("event")
        .filter(
            manor=locked_manor,
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[ArenaCoopEvent.Status.RECRUITING, ArenaCoopEvent.Status.PREPARING],
        )
        .order_by("-joined_at", "-id")
        .first()
    )
    if entry is None:
        raise ArenaCancellationError("当前没有可撤销的共斗报名")

    event = ArenaCoopEvent.objects.select_for_update().get(pk=entry.event_id)
    if event.status not in [ArenaCoopEvent.Status.RECRUITING, ArenaCoopEvent.Status.PREPARING]:
        raise ArenaCancellationError("活动已开战，当前不可撤销报名")

    entry.status = ArenaCoopEntry.Status.CANCELLED
    entry.cancelled_at = timezone.now()
    entry.save(update_fields=["status", "cancelled_at"])
    _release_entry_guest_statuses(entry)
    _update_daily_counter_locked(locked_manor, delta=-1)

    remaining = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count()
    if event.status == ArenaCoopEvent.Status.PREPARING and remaining < event.player_limit:
        event.status = ArenaCoopEvent.Status.RECRUITING
        event.prepare_ends_at = None
        event.save(update_fields=["status", "prepare_ends_at", "updated_at"])

    return 1


def run_due_arena_coop_events(*, now: datetime | None = None, limit: int = 20) -> int:
    current_time = now or timezone.now()
    candidate_ids = list(
        ArenaCoopEvent.objects.filter(
            status=ArenaCoopEvent.Status.PREPARING,
            prepare_ends_at__lte=current_time,
        )
        .order_by("prepare_ends_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    processed = 0

    for event_id in candidate_ids:
        with transaction.atomic():
            locked_event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
            if not locked_event:
                continue
            if locked_event.status != ArenaCoopEvent.Status.PREPARING:
                continue
            if not locked_event.prepare_ends_at or locked_event.prepare_ends_at > current_time:
                continue

            registered_entries = list(
                locked_event.entries.select_for_update()
                .filter(status=ArenaCoopEntry.Status.REGISTERED)
                .select_related("manor")
                .order_by("joined_at", "id")
            )
            if len(registered_entries) < locked_event.player_limit:
                locked_event.status = ArenaCoopEvent.Status.RECRUITING
                locked_event.prepare_ends_at = None
                locked_event.save(update_fields=["status", "prepare_ends_at", "updated_at"])
                continue

            locked_event.status = ArenaCoopEvent.Status.RUNNING
            locked_event.started_at = current_time
            locked_event.save(update_fields=["status", "started_at", "updated_at"])

            report = _run_coop_battle_locked(locked_event, current_time)
            _settle_coop_event_locked(locked_event, report, registered_entries, current_time)
            processed += 1

    return processed


def cleanup_expired_arena_coop_events(*, now: datetime, grace_seconds: int, limit: int = 50) -> int:
    retention_seconds = max(0, int(grace_seconds))
    cutoff_time = now - timedelta(seconds=retention_seconds)
    stale_ids = list(
        ArenaCoopEvent.objects.filter(
            status__in=[ArenaCoopEvent.Status.COMPLETED, ArenaCoopEvent.Status.CANCELLED],
            ended_at__isnull=False,
            ended_at__lte=cutoff_time,
        )
        .order_by("ended_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    if not stale_ids:
        return 0

    ArenaCoopEvent.objects.filter(id__in=stale_ids).delete()
    return len(stale_ids)
