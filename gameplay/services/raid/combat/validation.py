from __future__ import annotations

from typing import Any

from core.exceptions import InvalidBattleSnapshotError
from gameplay.services.battle_snapshots import build_guest_snapshot_proxies, validate_battle_troop_loadout

from ....models import RaidRun


def validate_raid_run_battle_payload(run: Any) -> None:
    """Validate durable player-raid battle inputs without mutating the run."""

    snapshots = run.guest_snapshots
    if not isinstance(snapshots, list):
        raise InvalidBattleSnapshotError(
            "踢馆门客快照数据无效",
            field_name="guest_snapshots",
        )
    if not snapshots and not run.guests.exists():
        raise InvalidBattleSnapshotError(
            "踢馆出征队伍缺少有效门客快照",
            snapshot_kind="attacker_lineup",
            field_name="guest_snapshots",
        )
    if snapshots:
        build_guest_snapshot_proxies(snapshots, include_guest_identity=True)
    validate_battle_troop_loadout(run.troop_loadout)


def raid_failure_reason_for_snapshot_error(exc: InvalidBattleSnapshotError) -> str:
    """Map persisted battle payload errors to the player-raid terminal reason."""

    if exc.snapshot_kind == "troop_loadout":
        return RaidRun.FailureReason.INVALID_TROOP_LOADOUT
    if exc.snapshot_kind == "attacker_lineup":
        return RaidRun.FailureReason.MISSING_ATTACKER_LINEUP
    return RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT
