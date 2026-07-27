from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from django.db.models import F

from battle.models import BattleReport
from battle.random_context import RNG_STREAM_RARE_DROP
from core.exceptions import MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.side_effects import schedule_best_effort_after_commit
from gameplay.models import ArenaCoopContribution, ArenaCoopEntry, ArenaCoopEvent, ItemTemplate, Manor, Message
from gameplay.services.inventory.core import add_item_to_inventory_locked

from .coop_battle import extract_boss_hp_snapshot, load_runtime_rules_for_event
from .coop_damage import aggregate_event_damage
from .coop_lifecycle import release_entry_guest_statuses
from .coop_rewards import build_reward_breakdown, rank_contribution_rows
from .replay import replay_context

ARENA_COOP_SETTLEMENT_MESSAGE_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)
CreateMessageFn = Callable[..., Message]


def grant_coop_reward_locked(locked_manor: Manor, *, total_coins: int, rare_drop_item_key: str) -> None:
    if total_coins > 0:
        locked_manor.arena_coins = F("arena_coins") + int(total_coins)
        locked_manor.save(update_fields=["arena_coins"])

    rare_drop_key = str(rare_drop_item_key or "").strip()
    if rare_drop_key.startswith("blueprint_"):
        from gameplay.services.buildings.forge import load_blueprint_catalog

        if rare_drop_key not in load_blueprint_catalog():
            raise AssertionError(f"invalid coop blueprint drop: {rare_drop_key}")
    if rare_drop_key and ItemTemplate.objects.filter(key=rare_drop_key).exists():
        add_item_to_inventory_locked(locked_manor, rare_drop_key, 1)


def format_rare_drop_summary(rare_drop_item_key: str) -> str:
    item_key = str(rare_drop_item_key or "").strip()
    if not item_key:
        return "未掉落稀有奖励"
    item_name = ItemTemplate.objects.filter(key=item_key).values_list("name", flat=True).first() or "未知物品"
    return f"掉落稀有奖励：{item_name}"


def send_coop_settlement_messages(
    *,
    locked_event: ArenaCoopEvent,
    locked_manor: Manor,
    contribution: ArenaCoopContribution,
    report: BattleReport,
    create_message_fn: CreateMessageFn,
) -> None:
    battle_title = "围攻光明顶战报"
    battle_body = f"围攻光明顶已结算，本场敌首为{locked_event.boss_name}，请查收战报。"
    reward_title = "围攻光明顶结算"
    reward_body = (
        f"总伤害 {contribution.total_damage}，"
        f"首领伤害 {contribution.boss_damage}，"
        f"排名第 {contribution.damage_rank}，"
        f"角斗币 {contribution.total_coins}。"
        f"{format_rare_drop_summary(contribution.rare_drop_item_key)}。"
    )

    create_message_fn(
        manor=locked_manor,
        kind=Message.Kind.BATTLE,
        title=battle_title,
        body=battle_body,
        battle_report=report,
    )
    create_message_fn(
        manor=locked_manor,
        kind=Message.Kind.REWARD,
        title=reward_title,
        body=reward_body,
    )


def settle_coop_event_locked(
    locked_event: ArenaCoopEvent,
    report: BattleReport,
    registered_entries: list[ArenaCoopEntry],
    now: datetime,
    *,
    base_rules: dict,
    logger: logging.Logger,
    create_message_fn: CreateMessageFn,
) -> None:
    rules = load_runtime_rules_for_event(base_rules, locked_event)
    damage_rows = aggregate_event_damage(report.rounds or [], boss_template_key=locked_event.boss_template_key)
    real_entries = [entry for entry in registered_entries if entry.source == ArenaCoopEntry.Source.PLAYER]
    virtual_entries = [entry for entry in registered_entries if entry.source == ArenaCoopEntry.Source.VIRTUAL]
    total_damage = sum(damage_rows.get(entry.id, {}).get("total_damage", 0) for entry in real_entries) or 1
    ranked_rows: list[dict] = []

    for entry in real_entries:
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
    rare_drop_rng = replay_context(locked_event).rng(RNG_STREAM_RARE_DROP)
    ranked_entry_ids = [entry.id for entry in registered_entries]
    entry_map = {entry.id: entry for entry in registered_entries}

    ArenaCoopContribution.objects.filter(event=locked_event).delete()
    contribution_rows: list[ArenaCoopContribution] = []
    for rank, row in enumerate(ranked_rows, start=1):
        row["damage_rank"] = rank
        reward_breakdown = build_reward_breakdown(
            row,
            rules=rules,
            boss_defeated=boss_defeated,
            rng=rare_drop_rng,
        )
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
    for entry in virtual_entries:
        bucket = damage_rows.get(entry.id, {"total_damage": 0, "boss_damage": 0, "guard_damage": 0})
        contribution_rows.append(
            ArenaCoopContribution(
                event=locked_event,
                entry=entry,
                total_damage=bucket["total_damage"],
                boss_damage=bucket["boss_damage"],
                guard_damage=bucket["guard_damage"],
                effective_damage=bucket["total_damage"],
                damage_share_bps=0,
                damage_rank=None,
                met_minimum_contribution=False,
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
        release_entry_guest_statuses(entry)

    for entry in locked_entries:
        if entry.source != ArenaCoopEntry.Source.PLAYER:
            continue
        locked_manor = Manor.objects.select_for_update().get(pk=entry.manor_id)
        contribution = contribution_map[entry.id]
        grant_coop_reward_locked(
            locked_manor,
            total_coins=contribution.total_coins,
            rare_drop_item_key=contribution.rare_drop_item_key,
        )

        def _send_settlement_messages(
            *,
            locked_event: ArenaCoopEvent = locked_event,
            locked_manor: Manor = locked_manor,
            contribution: ArenaCoopContribution = contribution,
            report: BattleReport = report,
            create_message_fn: CreateMessageFn = create_message_fn,
        ) -> None:
            send_coop_settlement_messages(
                locked_event=locked_event,
                locked_manor=locked_manor,
                contribution=contribution,
                report=report,
                create_message_fn=create_message_fn,
            )

        schedule_best_effort_after_commit(
            _send_settlement_messages,
            logger=logger,
            log_message=(
                "arena coop settlement messages failed: "
                f"event_id={locked_event.id} manor_id={locked_manor.id} entry_id={contribution.entry_id}"
            ),
            expected_exceptions=ARENA_COOP_SETTLEMENT_MESSAGE_EXCEPTIONS,
            degraded_component="arena_coop_messages",
        )

    locked_event.status = ArenaCoopEvent.Status.COMPLETED
    locked_event.battle_report = report
    locked_event.boss_defeated = boss_defeated
    locked_event.started_at = locked_event.started_at or now
    locked_event.ended_at = now
    boss_hp_snapshot = extract_boss_hp_snapshot(report, boss_template_key=locked_event.boss_template_key)
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
