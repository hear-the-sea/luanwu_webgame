from __future__ import annotations

from typing import Any, Callable, Dict, List, Set, Tuple

from core.exceptions import MessageError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS


def _require_mapping_payload(raw: Any, *, field_name: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return raw


def _require_sequence_payload(raw: Any, *, field_name: str) -> List[Any]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return list(raw)


def build_defense_report_if_needed(
    locked_run: Any,
    *,
    guest_template_rarity_rank_case: Callable[[str], Any],
    generate_sync_battle_report: Callable[..., Any],
) -> Any:
    report = locked_run.battle_report
    if report or locked_run.is_retreating or not locked_run.mission.is_defense:
        return report

    from guests.models import GuestStatus

    from ...models import PlayerTroop

    defender_guests = list(
        locked_run.manor.guests.select_for_update()
        .filter(status=GuestStatus.IDLE)
        .select_related("template")
        .prefetch_related("skills")
        .annotate(_template_rarity_rank=guest_template_rarity_rank_case("template__rarity"))
        .order_by("-_template_rarity_rank", "-level", "id")
    )
    defender_loadout = {
        troop.troop_template.key: troop.count
        for troop in (
            PlayerTroop.objects.select_for_update()
            .filter(manor=locked_run.manor, count__gt=0)
            .select_related("troop_template")
        )
    }
    report = generate_sync_battle_report(
        manor=locked_run.manor,
        mission=locked_run.mission,
        guests=defender_guests,
        loadout=defender_loadout,
        defender_setup={},
        travel_seconds=0,
        seed=locked_run.id,
    )
    locked_run.battle_report = report
    locked_run.save(update_fields=["battle_report"])
    return report


def extract_report_guest_state(report: Any, player_side: str) -> Tuple[Dict[int, int], Set[int], Set[int]]:
    hp_updates: Dict[int, int] = {}
    defeated_guest_ids: Set[int] = set()
    participant_ids: Set[int] = set()

    if not report:
        return hp_updates, defeated_guest_ids, participant_ids

    raw_losses = getattr(report, "losses", None)
    if raw_losses is None:
        raise AssertionError("invalid mission report.losses: None")
    losses = _require_mapping_payload(raw_losses, field_name="report.losses")
    side_losses = _require_mapping_payload(losses.get(player_side), field_name=f"report.losses.{player_side}")
    loss_updates = _require_mapping_payload(side_losses.get("hp_updates"), field_name="report.losses.hp_updates")
    for gid, hp in loss_updates.items():
        try:
            gid_int = int(gid)
            hp_int = int(hp)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid mission report hp update payload: {(gid, hp)!r}") from exc
        if gid_int <= 0:
            raise AssertionError(f"invalid mission report hp update payload: {(gid, hp)!r}")
        if hp_int < 0:
            raise AssertionError(f"invalid mission report hp update payload: {(gid, hp)!r}")
        hp_updates[gid_int] = hp_int

    team_entries = report.defender_team if player_side == "defender" else report.attacker_team
    if team_entries is None:
        raise AssertionError("invalid mission report.team_entries: None")
    for entry in _require_sequence_payload(team_entries, field_name="report.team_entries"):
        if not isinstance(entry, dict):
            raise AssertionError(f"invalid mission report team entry: {entry!r}")
        guest_id_raw = entry.get("guest_id")
        remaining_hp_raw = entry.get("remaining_hp")
        if guest_id_raw is None or remaining_hp_raw is None:
            raise AssertionError(f"invalid mission report team entry: {entry!r}")
        try:
            gid_int = int(guest_id_raw)
            remaining_int = int(remaining_hp_raw)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid mission report team entry: {entry!r}") from exc
        if gid_int <= 0:
            raise AssertionError(f"invalid mission report team entry: {entry!r}")
        if remaining_int < 0:
            raise AssertionError(f"invalid mission report team entry: {entry!r}")
        participant_ids.add(gid_int)
        hp_updates.setdefault(gid_int, remaining_int)
        if remaining_int <= 0:
            defeated_guest_ids.add(gid_int)

    return hp_updates, defeated_guest_ids, participant_ids


def select_guests_for_finalize(locked_run: Any, report: Any, participant_ids: Set[int]) -> List[Any]:
    if locked_run.is_retreating:
        return list(locked_run.guests.select_for_update())
    if report and participant_ids:
        return list(locked_run.manor.guests.select_for_update().filter(id__in=participant_ids))
    return list(locked_run.guests.select_for_update())


def prepare_guest_updates_for_finalize(
    guests: List[Any],
    *,
    is_retreating: bool,
    defeated_guest_ids: Set[int],
    hp_updates: Dict[int, int],
    now: Any,
) -> Tuple[List[Any], List[str]]:
    from guests.models import GuestStatus
    from guests.services.loyalty import clear_injury_loyalty_decay, start_injury_loyalty_decay

    guests_to_update: List[Any] = []
    for guest in guests:
        if is_retreating:
            guest.status = GuestStatus.IDLE
            clear_injury_loyalty_decay(guest)
        else:
            guest.status = GuestStatus.INJURED if guest.id in defeated_guest_ids else GuestStatus.IDLE
            if guest.status == GuestStatus.INJURED:
                start_injury_loyalty_decay(guest, now=now)
            else:
                clear_injury_loyalty_decay(guest)
            target_hp = hp_updates.get(guest.id)
            if target_hp is not None:
                guest.current_hp = max(1, min(guest.max_hp, target_hp))
                guest.last_hp_recovery_at = now
        guests_to_update.append(guest)

    fields = (
        ["status", "injury_loyalty_processed_at"]
        if is_retreating
        else ["status", "current_hp", "last_hp_recovery_at", "injury_loyalty_processed_at"]
    )
    return guests_to_update, fields


def return_attacker_troops_after_mission(locked_run: Any, report: Any, *, logger: Any) -> None:
    if locked_run.mission.is_defense:
        return

    from ..recruitment.troops import _return_surviving_troops_batch

    loadout = _require_mapping_payload(getattr(locked_run, "troop_loadout", None), field_name="troop_loadout")
    if not loadout:
        return

    if locked_run.is_retreating:
        if report:
            logger.warning(
                "撤退但存在战报，按战报归还护院: run_id=%s",
                locked_run.id,
                extra={"run_id": locked_run.id, "manor_id": locked_run.manor.id},
            )
            _return_surviving_troops_batch(locked_run.manor, loadout, report)
            return

        _return_surviving_troops_batch(locked_run.manor, loadout)
        return

    if not report:
        logger.warning(
            "任务完成但无战报，全额归还护院: run_id=%s",
            locked_run.id,
            extra={"run_id": locked_run.id, "manor_id": locked_run.manor.id},
        )
        _return_surviving_troops_batch(locked_run.manor, loadout)
        return

    _return_surviving_troops_batch(locked_run.manor, loadout, report)


def build_mission_drops_with_salvage(
    locked_run: Any,
    report: Any,
    player_side: str,
    *,
    logger: Any,
    resolve_defense_drops_if_missing: Callable[..., Dict[str, int]],
) -> Dict[str, int]:
    raw_drops = getattr(report, "drops", None)
    if raw_drops is None:
        raise AssertionError("invalid mission report.drops: None")
    drops = dict(_require_mapping_payload(raw_drops, field_name="report.drops"))
    if locked_run.mission.is_defense and not drops:
        drops = resolve_defense_drops_if_missing(
            report,
            _require_mapping_payload(getattr(locked_run.mission, "drop_table", None), field_name="drop table"),
        )

    from ..battle_salvage import calculate_battle_salvage

    exp_fruit_count, equipment_recovery = calculate_battle_salvage(
        report,
        equipment_casualty_side=player_side,
    )
    if exp_fruit_count > 0:
        drops["experience_fruit"] = drops.get("experience_fruit", 0) + exp_fruit_count
    for equip_key, count in equipment_recovery.items():
        drops[equip_key] = drops.get(equip_key, 0) + count

    return drops


def apply_mission_rewards_if_won(
    locked_run: Any,
    report: Any,
    player_side: str,
    *,
    logger: Any,
    resolve_defense_drops_if_missing: Callable[..., Dict[str, int]],
    award_mission_drops_locked: Callable[..., None],
) -> None:
    if not report:
        return
    if locked_run.is_retreating or report.winner != player_side:
        return

    drops = build_mission_drops_with_salvage(
        locked_run,
        report,
        player_side,
        logger=logger,
        resolve_defense_drops_if_missing=resolve_defense_drops_if_missing,
    )
    if not drops:
        return

    report.drops = drops
    report.save(update_fields=["drops"])
    award_mission_drops_locked(locked_run.manor, drops, locked_run.mission.name)


def send_mission_report_message(
    locked_run: Any,
    report: Any,
    *,
    logger: Any,
    create_message: Callable[..., Any],
    notify_user: Callable[..., Any],
    notification_infrastructure_exceptions: tuple[type[Exception], ...],
) -> None:
    if not report or locked_run.is_retreating:
        return

    try:
        create_message(
            manor=locked_run.manor,
            kind="battle",
            title=f"{locked_run.mission.name} 战报",
            body="",
            battle_report=report,
        )
    except MessageError as exc:
        logger.error(
            "Mission report message creation failed: run_id=%s manor_id=%s error=%s",
            locked_run.id,
            locked_run.manor_id,
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "mission_report_message", "run_id": locked_run.id},
        )
        return
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.error(
            "Mission report message creation failed: run_id=%s manor_id=%s error=%s",
            locked_run.id,
            locked_run.manor_id,
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "mission_report_message", "run_id": locked_run.id},
        )
        return

    try:
        notify_user(
            locked_run.manor.user_id,
            {
                "kind": "battle",
                "title": f"{locked_run.mission.name} 战报",
                "report_id": report.id,
                "mission_key": locked_run.mission.key,
                "mission_name": locked_run.mission.name,
            },
            log_context="mission battle notification",
        )
    except notification_infrastructure_exceptions as exc:
        logger.warning(
            "mission report notification failed: run_id=%s manor_id=%s report_id=%s error=%s",
            locked_run.id,
            locked_run.manor_id,
            getattr(report, "id", None),
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "mission_notification", "run_id": locked_run.id},
        )
