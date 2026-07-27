from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.exceptions import InvalidBattleSnapshotError
from gameplay.models import ArenaCoopEvent, ArenaMatch, RaidRun
from gameplay.services.battle_snapshots import build_guest_snapshot_proxies, validate_battle_troop_loadout
from gameplay.services.raid.combat.failure import fail_raid_run_and_release_resources
from gameplay.services.raid.combat.validation import (
    raid_failure_reason_for_snapshot_error,
    validate_raid_run_battle_payload,
)
from guilds.models import GuildRaidRun
from guilds.services.guild_raid_failure import fail_guild_raid_and_release_resources
from guilds.services.guild_raids import process_guild_raid_battle

logger = logging.getLogger(__name__)


def _parse_datetime_option(raw: str | None, *, option_name: str) -> datetime | None:
    if raw is None:
        return None
    if not raw.strip():
        raise CommandError(f"{option_name} 必须是 ISO-8601 时间")
    parsed = parse_datetime(raw)
    if parsed is None:
        raise CommandError(f"{option_name} 必须是 ISO-8601 时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _bounded(queryset: QuerySet, *, limit: int) -> QuerySet:
    return queryset[:limit]


def _require_positive_int(value: object, *, option_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CommandError(f"{option_name} 必须是正整数，收到 {value!r}")
    return value


def _parse_positive_ids(raw_values: list[object], *, option_name: str) -> list[int]:
    return [_require_positive_int(value, option_name=option_name) for value in raw_values]


def _snapshot_failure_reason(run: Any, *, guild: bool) -> tuple[str, str] | None:
    try:
        if guild:
            snapshots = run.guest_snapshots
            if not isinstance(snapshots, list):
                raise InvalidBattleSnapshotError(
                    "门客快照数据无效",
                    field_name="guest_snapshots",
                )
            if not snapshots:
                raise InvalidBattleSnapshotError(
                    "出征队伍缺少有效门客快照",
                    snapshot_kind="attacker_lineup",
                    field_name="guest_snapshots",
                )
            build_guest_snapshot_proxies(snapshots, include_guest_identity=True)
            validate_battle_troop_loadout(run.troop_loadout)
        else:
            validate_raid_run_battle_payload(run)
    except InvalidBattleSnapshotError as exc:
        if not guild:
            return raid_failure_reason_for_snapshot_error(exc), str(exc)
        reasons = GuildRaidRun.FailureReason
        if exc.snapshot_kind == "troop_loadout":
            reason = reasons.INVALID_TROOP_LOADOUT
        elif exc.snapshot_kind == "attacker_lineup":
            reason = reasons.MISSING_ATTACKER_LINEUP
        else:
            reason = reasons.INVALID_GUEST_SNAPSHOT
        return reason, str(exc)
    return None


class Command(BaseCommand):
    help = "只读审计 PVP、竞技场和共斗异常状态；显式 --apply 才执行安全领域补偿"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="通过正式领域服务执行可安全自动化的补偿")
        parser.add_argument("--raid-run-id", type=int, action="append", default=[])
        parser.add_argument("--guild-raid-run-id", type=int, action="append", default=[])
        parser.add_argument("--since", type=str, default=None, help="started_at/created_at 下界（ISO-8601）")
        parser.add_argument("--before", type=str, default=None, help="started_at/created_at 上界（ISO-8601）")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options) -> None:
        apply_changes = bool(options["apply"])
        limit = _require_positive_int(options["limit"], option_name="--limit")
        raid_ids = _parse_positive_ids(options["raid_run_id"], option_name="--raid-run-id")
        guild_run_ids = _parse_positive_ids(
            options["guild_raid_run_id"],
            option_name="--guild-raid-run-id",
        )
        since = _parse_datetime_option(options.get("since"), option_name="--since")
        before = _parse_datetime_option(options.get("before"), option_name="--before")
        now = timezone.now()
        findings: list[dict[str, Any]] = []

        raid_runs = (
            RaidRun.objects.filter(
                status__in=[RaidRun.Status.MARCHING, RaidRun.Status.BATTLING],
            )
            .prefetch_related("guests")
            .order_by("battle_at", "id")
        )
        if raid_ids:
            raid_runs = raid_runs.filter(pk__in=raid_ids)
        if since:
            raid_runs = raid_runs.filter(started_at__gte=since)
        if before:
            raid_runs = raid_runs.filter(started_at__lte=before)
        for run in _bounded(raid_runs, limit=limit):
            invalid = _snapshot_failure_reason(run, guild=False)
            overdue = bool(run.battle_at and run.battle_at <= now)
            if not invalid and not overdue:
                continue
            action = "fail_and_release_resources" if invalid else "retry_due_scanner"
            finding = {
                "model": "RaidRun",
                "id": run.pk,
                "status": run.status,
                "overdue": overdue,
                "suggested_action": action,
                "resource_impact": {
                    "guest_count": run.guests.count(),
                    "troop_loadout": run.troop_loadout,
                },
            }
            if invalid:
                finding["failure_reason"], finding["detail"] = invalid
                if apply_changes:
                    finding["applied"] = fail_raid_run_and_release_resources(
                        run.pk,
                        failure_reason=invalid[0],
                        now=now,
                        failure_detail=f"management audit: {invalid[1]}",
                    )
            findings.append(finding)

        legacy_failed_runs = (
            RaidRun.objects.filter(
                status=RaidRun.Status.FAILED,
                failure_reason=RaidRun.FailureReason.MISSING_ATTACKER_LINEUP,
                resources_released=False,
                battle_report__isnull=True,
            )
            .prefetch_related("guests")
            .order_by("completed_at", "id")
        )
        if raid_ids:
            legacy_failed_runs = legacy_failed_runs.filter(pk__in=raid_ids)
        if since:
            legacy_failed_runs = legacy_failed_runs.filter(started_at__gte=since)
        if before:
            legacy_failed_runs = legacy_failed_runs.filter(started_at__lte=before)
        for run in _bounded(legacy_failed_runs, limit=limit):
            finding = {
                "model": "RaidRun",
                "id": run.pk,
                "status": run.status,
                "failure_reason": run.failure_reason,
                "legacy_unreleased_resources": True,
                "suggested_action": "release_legacy_failed_resources",
                "resource_impact": {
                    "guest_count": run.guests.count(),
                    "troop_loadout": run.troop_loadout,
                },
            }
            if apply_changes:
                finding["applied"] = fail_raid_run_and_release_resources(
                    run.pk,
                    failure_reason=run.failure_reason,
                    now=now,
                    failure_detail="management audit: legacy FAILED raid resources were not released",
                )
            findings.append(finding)

        guild_runs = (
            GuildRaidRun.objects.select_related("attacker_guild", "defender_guild")
            .filter(
                status__in=[
                    GuildRaidRun.Status.MARCHING,
                    GuildRaidRun.Status.BATTLING,
                    GuildRaidRun.Status.RETURNING,
                    GuildRaidRun.Status.RETREATED,
                ]
            )
            .order_by("battle_at", "return_at", "id")
        )
        if guild_run_ids:
            guild_runs = guild_runs.filter(pk__in=guild_run_ids)
        if since:
            guild_runs = guild_runs.filter(started_at__gte=since)
        if before:
            guild_runs = guild_runs.filter(started_at__lte=before)
        for run in _bounded(guild_runs, limit=limit):
            invalid = (
                _snapshot_failure_reason(run, guild=True)
                if run.status
                in {
                    GuildRaidRun.Status.MARCHING,
                    GuildRaidRun.Status.BATTLING,
                }
                else None
            )
            inactive_attacker = not run.attacker_guild.is_active
            inactive_defender = not run.defender_guild.is_active
            due_at = (
                run.battle_at
                if run.status
                in {
                    GuildRaidRun.Status.MARCHING,
                    GuildRaidRun.Status.BATTLING,
                }
                else run.return_at
            )
            overdue = bool(due_at and due_at <= now)
            if not (invalid or inactive_attacker or inactive_defender or overdue):
                continue
            if inactive_attacker:
                action = "fail_inactive_attacker_and_release_resources"
            elif inactive_defender and run.status == GuildRaidRun.Status.MARCHING:
                action = "system_retreat_inactive_defender"
            elif invalid:
                action = "fail_and_release_resources"
            else:
                action = "retry_due_scanner"
            finding = {
                "model": "GuildRaidRun",
                "id": run.pk,
                "status": run.status,
                "overdue": overdue,
                "inactive_attacker": inactive_attacker,
                "inactive_defender": inactive_defender,
                "suggested_action": action,
                "resource_impact": {"troop_loadout": run.troop_loadout},
            }
            if invalid:
                finding["failure_reason"], finding["detail"] = invalid
            if apply_changes and inactive_attacker:
                finding["applied"] = fail_guild_raid_and_release_resources(
                    run.pk,
                    failure_reason=GuildRaidRun.FailureReason.INACTIVE_ATTACKER_GUILD,
                    now=now,
                    failure_detail="management audit: inactive attacker guild",
                    audit_event="inactive_guild_raid_blocked",
                )
            elif apply_changes and inactive_defender and run.status == GuildRaidRun.Status.MARCHING:
                finding["applied"] = process_guild_raid_battle(run, now=now)
            elif apply_changes and invalid:
                finding["applied"] = fail_guild_raid_and_release_resources(
                    run.pk,
                    failure_reason=invalid[0],
                    now=now,
                    failure_detail=f"management audit: {invalid[1]}",
                )
            findings.append(finding)

        duplicate_slots = _bounded(
            ArenaMatch.objects.values("tournament_id", "round_number", "match_index")
            .annotate(row_count=Count("id"))
            .filter(row_count__gt=1)
            .order_by("tournament_id", "round_number", "match_index"),
            limit=limit,
        )
        for row in duplicate_slots:
            findings.append(
                {
                    "model": "ArenaMatch",
                    "id": None,
                    "status": "integrity_violation",
                    "suggested_action": "manual_review_duplicate_slot",
                    **row,
                }
            )

        invalid_matches = ArenaMatch.objects.filter(
            ~Q(attacker_entry__tournament_id=F("tournament_id"))
            | (Q(defender_entry__isnull=False) & ~Q(defender_entry__tournament_id=F("tournament_id")))
            | (
                Q(winner_entry__isnull=False)
                & ~Q(winner_entry_id=F("attacker_entry_id"))
                & ~Q(winner_entry_id=F("defender_entry_id"))
            )
        ).order_by("id")
        for match in _bounded(invalid_matches, limit=limit):
            findings.append(
                {
                    "model": "ArenaMatch",
                    "id": match.pk,
                    "status": match.status,
                    "suggested_action": "manual_review_cross_tournament_or_winner",
                }
            )

        coop_events = ArenaCoopEvent.objects.filter(
            status=ArenaCoopEvent.Status.PREPARING,
            prepare_ends_at__lte=now,
        ).order_by("prepare_ends_at", "id")
        if since:
            coop_events = coop_events.filter(created_at__gte=since)
        if before:
            coop_events = coop_events.filter(created_at__lte=before)
        for event in _bounded(coop_events, limit=limit):
            findings.append(
                {
                    "model": "ArenaCoopEvent",
                    "id": event.pk,
                    "status": event.status,
                    "suggested_action": "run_due_arena_coop_scanner",
                    "registered_entry_count": event.entries.filter(status="registered").count(),
                    "prepare_ends_at": event.prepare_ends_at.isoformat() if event.prepare_ends_at else None,
                }
            )

        mode = "apply" if apply_changes else "dry-run"
        self.stdout.write(f"mode={mode} findings={len(findings)}")
        for finding in findings:
            self.stdout.write(json.dumps(finding, ensure_ascii=False, default=str, sort_keys=True))
            logger.info(
                "pvp_arena_state_audit: %s",
                finding,
                extra={
                    "event": "pvp_arena_state_audit",
                    "audit_mode": mode,
                    "audit_model": finding.get("model"),
                    "record_id": finding.get("id"),
                    "suggested_action": finding.get("suggested_action"),
                },
            )
