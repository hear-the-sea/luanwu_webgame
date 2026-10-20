from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from battle.random_context import RNG_STREAM_COMBAT, RNG_STREAM_TIE_BREAK, current_replay_metadata
from battle.replay_audit import audit_battle_replay_metadata
from battle.services import simulate_report
from core.config import GUEST
from core.exceptions import (
    ArenaBusyError,
    ArenaCancellationError,
    ArenaEntryStateError,
    ArenaGuestSelectionError,
    ArenaParticipationLimitError,
    InvalidBattleSnapshotError,
)
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock
from gameplay.models import ArenaWheelBout, ArenaWheelEntry, ArenaWheelEntryGuest, ArenaWheelEvent, Manor, Message
from gameplay.services.arena import helpers as arena_helpers
from gameplay.services.arena.core import ARENA_DAILY_PARTICIPATION_LIMIT, ARENA_REGISTRATION_SILVER_COST
from gameplay.services.arena.match_helpers import resolve_report_winner
from gameplay.services.arena.registration_helpers import (
    deduct_registration_silver_locked,
    load_selected_registration_guests_locked,
)
from gameplay.services.arena.replay import replay_context
from gameplay.services.arena.ring_rewards import credit_coins
from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot
from gameplay.services.utils.messages import bulk_create_messages
from guests.guest_rules import compute_guest_attack_attribute_component
from guests.models import Guest, GuestStatus
from guests.services.status import persist_guest_status_transitions

logger = logging.getLogger(__name__)

ARENA_WHEEL_PLAYER_LIMIT = 6
ARENA_WHEEL_MAX_GUESTS_PER_ENTRY = 10
ARENA_WHEEL_PREPARE_DURATION_SECONDS = 120
ARENA_WHEEL_COMPLETED_RETENTION_SECONDS = 86400
ARENA_WHEEL_REGISTRATION_LOCK_KEY = "arena:wheel:recruiting_event:create"
ARENA_WHEEL_REGISTRATION_LOCK_TIMEOUT = 30
ARENA_WHEEL_WIN_REWARD = 200
ARENA_WHEEL_LOSS_REWARD = 50
ARENA_WHEEL_DEBUFF_PER_STACK = 0.05
ARENA_WHEEL_MAX_DEBUFF_STACKS = 2
ARENA_WHEEL_BOUT_INTERVAL_SECONDS = 5 * 60


def _get_arena_wheel_debuff_multiplier(debuff_stacks: int) -> float:
    stacks = min(ARENA_WHEEL_MAX_DEBUFF_STACKS, max(0, int(debuff_stacks)))
    return 1 - ARENA_WHEEL_DEBUFF_PER_STACK * stacks


def get_arena_wheel_debuffed_defense_and_max_hp(
    *,
    defense_stat: int,
    max_hp: int,
    debuff_stacks: int,
) -> tuple[int, int]:
    original_defense_stat = max(0, int(defense_stat))
    debuffed_defense_stat = max(0, int(original_defense_stat * _get_arena_wheel_debuff_multiplier(debuff_stacks)))
    defense_hp_loss = (original_defense_stat - debuffed_defense_stat) * int(GUEST.DEFENSE_TO_HP_MULTIPLIER)
    debuffed_max_hp = max(int(GUEST.MIN_HP_FLOOR), int(max_hp) - defense_hp_loss)
    return debuffed_defense_stat, debuffed_max_hp


@dataclass(frozen=True)
class ArenaWheelRegistrationResult:
    entry: ArenaWheelEntry
    event: ArenaWheelEvent
    moved_to_preparing: bool
    entry_count: int


def _normalize_guest_ids(guest_ids: Iterable[int]) -> list[int]:
    selected = arena_helpers.normalize_guest_ids(
        guest_ids,
        max_guests_per_entry=ARENA_WHEEL_MAX_GUESTS_PER_ENTRY,
    )
    if len(selected) != ARENA_WHEEL_MAX_GUESTS_PER_ENTRY:
        raise ArenaGuestSelectionError(f"三对三车轮战需要恰好选择 {ARENA_WHEEL_MAX_GUESTS_PER_ENTRY} 名门客")
    return selected


def _get_or_create_recruiting_event_locked() -> ArenaWheelEvent:
    event = (
        ArenaWheelEvent.objects.select_for_update()
        .filter(status=ArenaWheelEvent.Status.RECRUITING)
        .order_by("created_at", "id")
        .first()
    )
    if event:
        return event

    acquired, from_cache, lock_token = acquire_best_effort_lock(
        ARENA_WHEEL_REGISTRATION_LOCK_KEY,
        timeout_seconds=ARENA_WHEEL_REGISTRATION_LOCK_TIMEOUT,
        logger=logger,
        log_context="arena wheel recruiting event lock",
        allow_local_fallback=False,
    )
    if not acquired:
        existing = (
            ArenaWheelEvent.objects.select_for_update()
            .filter(status=ArenaWheelEvent.Status.RECRUITING)
            .order_by("created_at", "id")
            .first()
        )
        if existing:
            return existing
        raise ArenaBusyError()

    def release_lock() -> None:
        release_best_effort_lock(
            ARENA_WHEEL_REGISTRATION_LOCK_KEY,
            from_cache=from_cache,
            lock_token=lock_token,
            logger=logger,
            log_context="arena wheel recruiting event lock",
        )

    try:
        event = (
            ArenaWheelEvent.objects.select_for_update()
            .filter(status=ArenaWheelEvent.Status.RECRUITING)
            .order_by("created_at", "id")
            .first()
        )
        if event is None:
            event = ArenaWheelEvent.objects.create(
                status=ArenaWheelEvent.Status.RECRUITING,
                player_limit=ARENA_WHEEL_PLAYER_LIMIT,
                guest_limit_per_entry=ARENA_WHEEL_MAX_GUESTS_PER_ENTRY,
                prepare_duration_seconds=ARENA_WHEEL_PREPARE_DURATION_SECONDS,
                **current_replay_metadata(),
            )
    except Exception:
        release_lock()
        raise

    # Keep the distributed lock until the outer registration transaction commits.
    # Releasing it before commit lets a concurrent request create a second event
    # while this event is still invisible to other transactions.
    transaction.on_commit(release_lock, robust=True)
    return event


def _move_to_preparing_locked(event: ArenaWheelEvent, *, now: datetime) -> bool:
    if event.status != ArenaWheelEvent.Status.RECRUITING:
        return False
    entry_count = event.entries.filter(status=ArenaWheelEntry.Status.REGISTERED).count()
    if entry_count < event.player_limit:
        return False
    entries = list(
        event.entries.select_for_update().filter(status=ArenaWheelEntry.Status.REGISTERED).order_by("joined_at", "id")
    )
    _split_and_order_entries(event, entries)
    event.status = ArenaWheelEvent.Status.PREPARING
    event.prepare_ends_at = now + timedelta(seconds=event.prepare_duration_seconds)
    event.save(update_fields=["status", "prepare_ends_at", "updated_at"])
    return True


@transaction.atomic
def register_arena_wheel_entry(manor: Manor, guest_ids: Iterable[int]) -> ArenaWheelRegistrationResult:
    selected_guest_ids = _normalize_guest_ids(guest_ids)
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    today_start, today_end = arena_helpers.today_bounds()
    daily_count = (
        ArenaWheelEntry.objects.filter(
            manor=locked_manor,
            joined_at__gte=today_start,
            joined_at__lt=today_end,
        )
        .exclude(status=ArenaWheelEntry.Status.CANCELLED)
        .count()
    )
    if daily_count >= ARENA_DAILY_PARTICIPATION_LIMIT:
        raise ArenaParticipationLimitError(ARENA_DAILY_PARTICIPATION_LIMIT)

    if ArenaWheelEntry.objects.filter(
        manor=locked_manor,
        status__in=[ArenaWheelEntry.Status.REGISTERED, ArenaWheelEntry.Status.ACTIVE],
        event__status__in=[
            ArenaWheelEvent.Status.RECRUITING,
            ArenaWheelEvent.Status.PREPARING,
            ArenaWheelEvent.Status.RUNNING,
        ],
    ).exists():
        raise ArenaEntryStateError("您已有进行中的三对三车轮战报名，请等待本场结束")

    selected_guests = load_selected_registration_guests_locked(locked_manor, selected_guest_ids)
    deduct_registration_silver_locked(locked_manor, silver_cost=ARENA_REGISTRATION_SILVER_COST)
    event = _get_or_create_recruiting_event_locked()
    entry = (
        ArenaWheelEntry.objects.select_for_update()
        .filter(event=event, manor=locked_manor, status=ArenaWheelEntry.Status.CANCELLED)
        .order_by("-joined_at", "-id")
        .first()
    )
    if entry is None:
        entry = ArenaWheelEntry.objects.create(event=event, manor=locked_manor)
    else:
        entry.status = ArenaWheelEntry.Status.REGISTERED
        entry.cancelled_at = None
        entry.joined_at = timezone.now()
        entry.save(update_fields=["status", "cancelled_at", "joined_at"])
        entry.entry_guests.all().delete()

    ArenaWheelEntryGuest.objects.bulk_create(
        [
            ArenaWheelEntryGuest(
                entry=entry,
                guest=guest,
                slot_index=index,
                snapshot=build_entry_guest_snapshot(guest),
            )
            for index, guest in enumerate(selected_guests)
        ]
    )
    persist_guest_status_transitions(selected_guests, GuestStatus.ARENA, source="arena_wheel_register")

    entry_count = event.entries.filter(status=ArenaWheelEntry.Status.REGISTERED).count()
    moved_to_preparing = _move_to_preparing_locked(event, now=timezone.now())
    return ArenaWheelRegistrationResult(entry, event, moved_to_preparing, entry_count)


@transaction.atomic
def cancel_arena_wheel_entry(manor: Manor) -> int:
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    entry = (
        ArenaWheelEntry.objects.select_for_update()
        .select_related("event")
        .filter(
            manor=locked_manor,
            status=ArenaWheelEntry.Status.REGISTERED,
            event__status=ArenaWheelEvent.Status.RECRUITING,
        )
        .order_by("-joined_at", "-id")
        .first()
    )
    if not entry:
        raise ArenaCancellationError("当前没有可撤销的三对三车轮战报名")
    event = ArenaWheelEvent.objects.select_for_update().get(pk=entry.event_id)
    if event.status != ArenaWheelEvent.Status.RECRUITING:
        raise ArenaCancellationError("车轮战已进入准备期，当前不可撤销报名")
    entry.status = ArenaWheelEntry.Status.CANCELLED
    entry.cancelled_at = timezone.now()
    entry.save(update_fields=["status", "cancelled_at"])
    _release_entry_guest_statuses(entry)
    return 1


def _release_entry_guest_statuses(entry: ArenaWheelEntry) -> None:
    guest_ids = list(entry.entry_guests.values_list("guest_id", flat=True))
    if not guest_ids:
        return
    guests = list(
        Guest.objects.select_for_update()
        .filter(id__in=guest_ids, status__in=[GuestStatus.ARENA, GuestStatus.DEPLOYED])
        .order_by("id")
    )
    persist_guest_status_transitions(guests, GuestStatus.IDLE, source="arena_wheel_release")


def _split_and_order_entries(
    event: ArenaWheelEvent,
    entries: list[ArenaWheelEntry],
) -> tuple[list[ArenaWheelEntry], list[ArenaWheelEntry]]:
    rng = replay_context(event).rng(RNG_STREAM_TIE_BREAK, discriminator="team_assignment")
    shuffled = list(entries)
    rng.shuffle(shuffled)
    teams = {ArenaWheelEvent.Team.A: shuffled[:3], ArenaWheelEvent.Team.B: shuffled[3:]}
    for team_name, members in teams.items():
        rng.shuffle(members)
        for index, entry in enumerate(members):
            entry.team = team_name
            entry.team_order = index + 1
            entry.status = ArenaWheelEntry.Status.REGISTERED
    ArenaWheelEntry.objects.bulk_update(
        [entry for team_entries in teams.values() for entry in team_entries],
        ["team", "team_order", "status"],
    )
    return teams[ArenaWheelEvent.Team.A], teams[ArenaWheelEvent.Team.B]


def _load_active_guest_proxies(
    entry: ArenaWheelEntry,
) -> tuple[list[ArenaGuestSnapshotProxy], dict[int, ArenaWheelEntryGuest]]:
    links = list(entry.entry_guests.order_by("slot_index", "id"))
    proxies: list[ArenaGuestSnapshotProxy] = []
    links_by_slot: dict[int, ArenaWheelEntryGuest] = {}
    for link in links:
        if not isinstance(link.snapshot, dict) or not link.snapshot:
            continue
        if link.snapshot.get("wheel_knocked_out"):
            continue
        try:
            proxy = ArenaGuestSnapshotProxy(link.snapshot)
        except InvalidBattleSnapshotError:
            logger.exception("invalid arena wheel guest snapshot: entry_id=%s guest_link_id=%s", entry.pk, link.pk)
            continue
        proxy._owner_entry_id = entry.pk
        proxy._combatant_slot = link.slot_index
        debuff_multiplier = _get_arena_wheel_debuff_multiplier(entry.debuff_stacks)
        attack_bonus = proxy.attack - proxy.attack_attribute_component
        proxy.force = max(0, int(proxy.force * debuff_multiplier))
        proxy.intellect = max(0, int(proxy.intellect * debuff_multiplier))
        proxy.agility = max(0, int(proxy.agility * debuff_multiplier))
        proxy.attack_attribute_component = compute_guest_attack_attribute_component(proxy)
        proxy.attack = max(1, proxy.attack_attribute_component + attack_bonus)
        proxy.defense_stat, proxy.max_hp = get_arena_wheel_debuffed_defense_and_max_hp(
            defense_stat=proxy.defense_stat,
            max_hp=proxy.max_hp,
            debuff_stacks=entry.debuff_stacks,
        )
        proxy.current_hp = min(proxy.current_hp, proxy.max_hp)
        proxy.defense = max(1, int(proxy.defense * debuff_multiplier))
        proxies.append(proxy)
        links_by_slot[link.slot_index] = link
    return proxies, links_by_slot


def _remaining_hp(team: object) -> int:
    members = team if isinstance(team, list) else []
    return sum(max(0, int(member.get("remaining_hp") or 0)) for member in members if isinstance(member, dict))


def _persist_report_hp(
    links_by_slot: dict[int, ArenaWheelEntryGuest],
    team_report: object,
) -> None:
    members = team_report if isinstance(team_report, list) else []
    member_by_slot = {
        int(member["combatant_slot"]): member
        for member in members
        if isinstance(member, dict) and member.get("combatant_slot") is not None
    }
    changed: list[ArenaWheelEntryGuest] = []
    for slot_index, link in links_by_slot.items():
        member = member_by_slot.get(slot_index)
        if member is None:
            continue
        remaining_hp = max(0, int(member.get("remaining_hp") or 0))
        snapshot = dict(link.snapshot)
        snapshot["wheel_knocked_out"] = remaining_hp <= 0
        snapshot["current_hp"] = max(1, min(int(snapshot.get("max_hp") or 1), remaining_hp))
        link.snapshot = snapshot
        changed.append(link)
    if changed:
        ArenaWheelEntryGuest.objects.bulk_update(changed, ["snapshot"])


def _next_entry(entries: list[ArenaWheelEntry], current: ArenaWheelEntry) -> ArenaWheelEntry | None:
    return next((entry for entry in entries if entry.team_order > current.team_order), None)


def _run_bout(
    *,
    event: ArenaWheelEvent,
    team_a_entry: ArenaWheelEntry,
    team_b_entry: ArenaWheelEntry,
    bout_number: int,
    context,
) -> tuple[ArenaWheelEntry | None, str]:
    team_a_guests, team_a_links = _load_active_guest_proxies(team_a_entry)
    team_b_guests, team_b_links = _load_active_guest_proxies(team_b_entry)
    team_a_stacks = int(team_a_entry.debuff_stacks)
    team_b_stacks = int(team_b_entry.debuff_stacks)
    side_rng = context.rng(RNG_STREAM_TIE_BREAK, discriminator=f"attacker_side:{bout_number}")
    team_a_is_attacker = bool(side_rng.randrange(2) == 0)
    bout_seed = context.persisted_seed(RNG_STREAM_COMBAT, discriminator=f"wheel:bout:{bout_number}")
    winner_entry: ArenaWheelEntry | None = None
    winner_note = ""
    report = None

    if not team_a_guests or not team_b_guests:
        if team_a_guests:
            winner_entry = team_a_entry
            winner_note = f"乙队第 {team_b_entry.team_order} 位玩家没有可出战门客，甲队判胜"
        elif team_b_guests:
            winner_entry = team_b_entry
            winner_note = f"甲队第 {team_a_entry.team_order} 位玩家没有可出战门客，乙队判胜"
        else:
            winner_note = "双方均无可出战门客，双方本轮各淘汰一名玩家"
    else:
        attacker_entry, defender_entry = (
            (team_a_entry, team_b_entry) if team_a_is_attacker else (team_b_entry, team_a_entry)
        )
        attacker_guests, defender_guests = (
            (team_a_guests, team_b_guests) if team_a_is_attacker else (team_b_guests, team_a_guests)
        )
        report = simulate_report(
            manor=attacker_entry.manor,
            battle_type="arena",
            seed=bout_seed,
            rng_version=event.rng_version,
            battle_engine_version=event.battle_engine_version,
            troop_loadout={},
            fill_default_troops=False,
            attacker_guests=cast(list[Guest], attacker_guests),
            defender_guests=cast(list[Guest], defender_guests),
            max_squad=event.guest_limit_per_entry,
            defender_max_squad=event.guest_limit_per_entry,
            auto_reward=False,
            send_message=False,
            apply_damage=False,
            use_lock=False,
            opponent_name=defender_entry.manor.display_name,
        )
        attacker_hp = _remaining_hp(report.attacker_team)
        defender_hp = _remaining_hp(report.defender_team)
        if attacker_hp > 0 and defender_hp <= 0:
            winner_entry = attacker_entry
            winner_note = "战斗结束时仅攻击方仍有存活门客"
        elif defender_hp > 0 and attacker_hp <= 0:
            winner_entry = defender_entry
            winner_note = "战斗结束时仅防守方仍有存活门客"
        elif attacker_hp <= 0 and defender_hp <= 0:
            winner_note = "双方门客全部倒下，双方本轮各淘汰一名玩家"
        else:
            winner_entry, winner_note = resolve_report_winner(
                report,
                attacker_entry=attacker_entry,
                defender_entry=defender_entry,
                rng=context.rng(RNG_STREAM_TIE_BREAK, discriminator=f"winner:{bout_number}"),
            )

        if team_a_is_attacker:
            _persist_report_hp(team_a_links, report.attacker_team)
            _persist_report_hp(team_b_links, report.defender_team)
        else:
            _persist_report_hp(team_b_links, report.attacker_team)
            _persist_report_hp(team_a_links, report.defender_team)

    bout = ArenaWheelBout.objects.create(
        event=event,
        bout_number=bout_number,
        team_a_entry=team_a_entry,
        team_b_entry=team_b_entry,
        winner_entry=winner_entry,
        battle_report=report,
        base_seed=bout_seed,
        rng_version=event.rng_version,
        battle_engine_version=event.battle_engine_version,
        team_a_is_attacker=team_a_is_attacker,
        team_a_debuff_stacks=team_a_stacks,
        team_b_debuff_stacks=team_b_stacks,
        winner_note=winner_note,
    )
    if report is not None:
        audit_battle_replay_metadata(bout, report, logger=logger, activity_kind="arena_wheel_bout")
    result = f"本场胜者：{winner_entry.manor.display_name}。" if winner_entry else "双方本轮各淘汰一名玩家。"
    body = f"{team_a_entry.manor.display_name} 对阵 {team_b_entry.manor.display_name}，{result}\n{winner_note}"
    entries = list(
        event.entries.select_related("manor")
        .filter(team__in=[ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B])
        .exclude(status=ArenaWheelEntry.Status.CANCELLED)
        .order_by("team", "team_order", "id")
    )
    team_names = {
        team: "、".join(entry.manor.display_name for entry in entries if entry.team == team)
        for team in (ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B)
    }
    opposing_teams = {ArenaWheelEvent.Team.A: ArenaWheelEvent.Team.B, ArenaWheelEvent.Team.B: ArenaWheelEvent.Team.A}
    active_entries = {ArenaWheelEvent.Team.A: team_a_entry, ArenaWheelEvent.Team.B: team_b_entry}
    attacker_team = ArenaWheelEvent.Team.A if team_a_is_attacker else ArenaWheelEvent.Team.B
    messages = []
    for entry in entries:
        entry_team = ArenaWheelEvent.Team(entry.team)
        opponent_team = opposing_teams[entry_team]
        messages.append(
            {
                "manor": entry.manor,
                "kind": Message.Kind.BATTLE,
                "title": f"金羽擂台 #{event.pk} 第 {bout_number} 轮战报",
                "body": (f"我方队伍：{team_names[entry_team]}\n" f"敌方队伍：{team_names[opponent_team]}\n" f"{body}"),
                "battle_report": report,
                "attachments": {
                    "arena_wheel": {
                        "player_side": "attacker" if entry_team == attacker_team else "defender",
                        "teams": [
                            {
                                "label": f"{label}（{team.label}）",
                                "members": team_names[team],
                                "active_player": active_entries[team].manor.display_name,
                            }
                            for team, label in (
                                (entry_team, "我方队伍"),
                                (opponent_team, "敌方队伍"),
                            )
                        ],
                    }
                },
            }
        )
    bulk_create_messages(messages)
    return winner_entry, winner_note


def _settle_event_locked(
    *,
    event: ArenaWheelEvent,
    entries: list[ArenaWheelEntry],
    now: datetime,
) -> None:
    manors = {
        manor.pk: manor
        for manor in Manor.objects.select_for_update()
        .filter(pk__in=[entry.manor_id for entry in entries])
        .order_by("pk")
    }
    for entry in entries:
        entry.status = (
            ArenaWheelEntry.Status.COMPLETED if entry.team == event.winner_team else ArenaWheelEntry.Status.ELIMINATED
        )
    ArenaWheelEntry.objects.bulk_update(entries, ["status"])

    for entry in entries:
        _release_entry_guest_statuses(entry)
        reward = ARENA_WHEEL_WIN_REWARD if entry.team == event.winner_team else ARENA_WHEEL_LOSS_REWARD
        reward_note = "金羽擂台胜方奖励" if entry.team == event.winner_team else "金羽擂台败方奖励"
        credit_coins(manors[entry.manor_id], reward, reward_note)

    event.status = ArenaWheelEvent.Status.COMPLETED
    event.ended_at = now
    event.save(update_fields=["status", "ended_at", "updated_at"])


def _process_wheel_event_locked(event: ArenaWheelEvent, *, entries: list[ArenaWheelEntry], now: datetime) -> bool:
    context = replay_context(event)
    team_a = sorted(
        (entry for entry in entries if entry.team == ArenaWheelEvent.Team.A),
        key=lambda entry: entry.team_order,
    )
    team_b = sorted(
        (entry for entry in entries if entry.team == ArenaWheelEvent.Team.B),
        key=lambda entry: entry.team_order,
    )
    active_a: ArenaWheelEntry | None = None
    active_b: ArenaWheelEntry | None = None
    if event.status == ArenaWheelEvent.Status.PREPARING:
        if len(team_a) != 3 or len(team_b) != 3:
            team_a, team_b = _split_and_order_entries(event, entries)
        first_team_a_entry = team_a[0]
        first_team_b_entry = team_b[0]
        first_team_a_entry.status = ArenaWheelEntry.Status.ACTIVE
        first_team_b_entry.status = ArenaWheelEntry.Status.ACTIVE
        ArenaWheelEntry.objects.bulk_update([first_team_a_entry, first_team_b_entry], ["status"])
        active_a = first_team_a_entry
        active_b = first_team_b_entry
        event.status = ArenaWheelEvent.Status.RUNNING
        event.started_at = now
        event.team_a_active_entry_id = first_team_a_entry.pk
        event.team_b_active_entry_id = first_team_b_entry.pk
        event.save(
            update_fields=["status", "started_at", "team_a_active_entry_id", "team_b_active_entry_id", "updated_at"]
        )
    else:
        active_a = next((entry for entry in team_a if entry.pk == event.team_a_active_entry_id), None)
        active_b = next((entry for entry in team_b if entry.pk == event.team_b_active_entry_id), None)
        active_a = active_a or next((entry for entry in team_a if entry.status == ArenaWheelEntry.Status.ACTIVE), None)
        active_b = active_b or next((entry for entry in team_b if entry.status == ArenaWheelEntry.Status.ACTIVE), None)

    if active_a is None or active_b is None:
        if active_a is not None:
            event.winner_team = ArenaWheelEvent.Team.A
        elif active_b is not None:
            event.winner_team = ArenaWheelEvent.Team.B
        else:
            event.winner_team = context.rng(RNG_STREAM_TIE_BREAK, discriminator="team_tiebreak").choice(
                [ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B]
            )
        event.team_a_active_entry_id = active_a.pk if active_a else None
        event.team_b_active_entry_id = active_b.pk if active_b else None
        event.save(update_fields=["winner_team", "team_a_active_entry_id", "team_b_active_entry_id", "updated_at"])
        _settle_event_locked(event=event, entries=entries, now=now)
        return True

    bout_number = int(event.current_bout_number) + 1
    winner, _note = _run_bout(
        event=event,
        team_a_entry=active_a,
        team_b_entry=active_b,
        bout_number=bout_number,
        context=context,
    )
    event.current_bout_number = bout_number
    if winner is active_a:
        active_b.status = ArenaWheelEntry.Status.ELIMINATED
        ArenaWheelEntry.objects.filter(pk=active_b.pk).update(status=active_b.status)
        next_b = _next_entry(team_b, active_b)
        if next_b:
            active_a.debuff_stacks = min(active_a.debuff_stacks + 1, ARENA_WHEEL_MAX_DEBUFF_STACKS)
            active_a.save(update_fields=["debuff_stacks"])
            next_b.status = ArenaWheelEntry.Status.ACTIVE
            next_b.save(update_fields=["status"])
        active_b = next_b
    elif winner is active_b:
        active_a.status = ArenaWheelEntry.Status.ELIMINATED
        ArenaWheelEntry.objects.filter(pk=active_a.pk).update(status=active_a.status)
        next_a = _next_entry(team_a, active_a)
        if next_a:
            active_b.debuff_stacks = min(active_b.debuff_stacks + 1, ARENA_WHEEL_MAX_DEBUFF_STACKS)
            active_b.save(update_fields=["debuff_stacks"])
            next_a.status = ArenaWheelEntry.Status.ACTIVE
            next_a.save(update_fields=["status"])
        active_a = next_a
    else:
        active_a.status = ArenaWheelEntry.Status.ELIMINATED
        active_b.status = ArenaWheelEntry.Status.ELIMINATED
        ArenaWheelEntry.objects.bulk_update([active_a, active_b], ["status"])
        active_a = _next_entry(team_a, active_a)
        active_b = _next_entry(team_b, active_b)
        if active_a:
            active_a.status = ArenaWheelEntry.Status.ACTIVE
            active_a.save(update_fields=["status"])
        if active_b:
            active_b.status = ArenaWheelEntry.Status.ACTIVE
            active_b.save(update_fields=["status"])

    event.team_a_active_entry_id = active_a.pk if active_a else None
    event.team_b_active_entry_id = active_b.pk if active_b else None
    if active_a is not None and active_b is not None:
        event.save(
            update_fields=[
                "current_bout_number",
                "team_a_active_entry_id",
                "team_b_active_entry_id",
                "updated_at",
            ]
        )
        return True

    if active_a is not None:
        event.winner_team = ArenaWheelEvent.Team.A
    elif active_b is not None:
        event.winner_team = ArenaWheelEvent.Team.B
    else:
        # A full-team double knockout leaves the randomly selected first team as the tiebreak winner.
        event.winner_team = context.rng(RNG_STREAM_TIE_BREAK, discriminator="team_tiebreak").choice(
            [ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B]
        )
    event.save(
        update_fields=[
            "current_bout_number",
            "team_a_active_entry_id",
            "team_b_active_entry_id",
            "winner_team",
            "updated_at",
        ]
    )
    _settle_event_locked(event=event, entries=entries, now=now)
    return True


def run_due_arena_wheel_events(*, now: datetime | None = None, limit: int = 20) -> int:
    current_time = now or timezone.now()
    due_bout_cutoff = current_time - timedelta(seconds=ARENA_WHEEL_BOUT_INTERVAL_SECONDS)
    candidate_ids = list(
        ArenaWheelEvent.objects.filter(
            Q(status=ArenaWheelEvent.Status.PREPARING, prepare_ends_at__lte=current_time)
            | Q(status=ArenaWheelEvent.Status.RUNNING, current_bout_number=0)
            | Q(
                status=ArenaWheelEvent.Status.RUNNING,
                bouts__bout_number=F("current_bout_number"),
                bouts__created_at__lte=due_bout_cutoff,
            )
        )
        .distinct()
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[: max(1, int(limit))]
    )
    processed = 0
    for event_id in candidate_ids:
        with transaction.atomic():
            manor_ids = list(
                ArenaWheelEntry.objects.filter(event_id=event_id)
                .exclude(status=ArenaWheelEntry.Status.CANCELLED)
                .order_by("manor_id")
                .values_list("manor_id", flat=True)
            )
            list(Manor.objects.select_for_update().filter(pk__in=manor_ids).order_by("pk"))
            event = ArenaWheelEvent.objects.select_for_update().filter(pk=event_id).first()
            if event is None:
                continue
            if event.status == ArenaWheelEvent.Status.PREPARING:
                if not event.prepare_ends_at or event.prepare_ends_at > current_time:
                    continue
            elif event.status == ArenaWheelEvent.Status.RUNNING:
                latest_bout = event.bouts.order_by("-bout_number", "-id").first()
                if (
                    latest_bout
                    and latest_bout.created_at + timedelta(seconds=ARENA_WHEEL_BOUT_INTERVAL_SECONDS) > current_time
                ):
                    continue
            else:
                continue
            entries = list(
                event.entries.select_for_update()
                .select_related("manor")
                .prefetch_related("entry_guests")
                .exclude(status=ArenaWheelEntry.Status.CANCELLED)
                .order_by("team", "team_order", "joined_at", "id")
            )
            if event.status == ArenaWheelEvent.Status.PREPARING and len(entries) != event.player_limit:
                event.status = ArenaWheelEvent.Status.CANCELLED
                event.ended_at = current_time
                event.save(update_fields=["status", "ended_at", "updated_at"])
                for entry in entries:
                    _release_entry_guest_statuses(entry)
                continue
            if _process_wheel_event_locked(event, entries=entries, now=current_time):
                processed += 1
    return processed


def cleanup_expired_arena_wheel_events(
    *,
    now: datetime,
    grace_seconds: int = ARENA_WHEEL_COMPLETED_RETENTION_SECONDS,
    limit: int = 50,
) -> int:
    cutoff_time = now - timedelta(seconds=max(0, int(grace_seconds)))
    with transaction.atomic():
        stale_ids = list(
            ArenaWheelEvent.objects.select_for_update()
            .filter(
                status__in=[ArenaWheelEvent.Status.COMPLETED, ArenaWheelEvent.Status.CANCELLED],
                ended_at__isnull=False,
                ended_at__lte=cutoff_time,
            )
            .order_by("ended_at", "id")
            .values_list("id", flat=True)[: max(1, int(limit))]
        )
        if stale_ids:
            ArenaWheelEvent.objects.filter(id__in=stale_ids).delete()
    return len(stale_ids)
