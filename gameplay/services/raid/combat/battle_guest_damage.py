from __future__ import annotations

from typing import Any

from guests.services.loyalty import start_injury_loyalty_decay


def extract_side_guest_state(report: Any, side: str) -> tuple[dict[int, int], set[int]]:
    hp_updates: dict[int, int] = {}
    defeated_guest_ids: set[int] = set()

    if not report:
        return hp_updates, defeated_guest_ids

    team_entries = report.attacker_team if side == "attacker" else report.defender_team
    for entry in team_entries or []:
        if not isinstance(entry, dict):
            continue
        guest_id_raw = entry.get("guest_id")
        remaining_hp_raw = entry.get("remaining_hp")
        try:
            guest_id = int(guest_id_raw)  # type: ignore[arg-type]
            remaining_hp = int(remaining_hp_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        hp_updates[guest_id] = remaining_hp
        if remaining_hp <= 0:
            defeated_guest_ids.add(guest_id)

    raw_hp_updates = ((report.losses or {}).get(side) or {}).get("hp_updates") or {}
    for guest_id_raw, hp_raw in raw_hp_updates.items():
        try:
            guest_id = int(guest_id_raw)
            hp = int(hp_raw)
        except (TypeError, ValueError):
            continue
        hp_updates.setdefault(guest_id, hp)

    return hp_updates, defeated_guest_ids


def apply_guest_damage_from_report(
    report: Any,
    *,
    attacker_guest_ids: set[int],
    defender_guest_ids: set[int],
    guest_model: Any,
    guest_status: Any,
    now: Any,
) -> None:
    attacker_hp_updates, attacker_defeated_ids = extract_side_guest_state(report, "attacker")
    defender_hp_updates, defender_defeated_ids = extract_side_guest_state(report, "defender")

    target_ids = (attacker_guest_ids | defender_guest_ids) & (
        set(attacker_hp_updates.keys()) | set(defender_hp_updates.keys())
    )
    if not target_ids:
        return

    guests = list(guest_model.objects.select_for_update().filter(id__in=target_ids))
    if not guests:
        return

    dirty_guests = []
    for guest in guests:
        if guest.id in attacker_guest_ids:
            hp = attacker_hp_updates.get(guest.id)
            is_defeated = guest.id in attacker_defeated_ids
        else:
            hp = defender_hp_updates.get(guest.id)
            is_defeated = guest.id in defender_defeated_ids
        if hp is None:
            continue

        guest.current_hp = max(1, min(guest.max_hp, int(hp)))
        guest.last_hp_recovery_at = now
        if is_defeated:
            guest.status = guest_status.INJURED
            start_injury_loyalty_decay(guest, now=now)
        dirty_guests.append(guest)

    if dirty_guests:
        guest_model.objects.bulk_update(
            dirty_guests,
            ["current_hp", "last_hp_recovery_at", "status", "injury_loyalty_processed_at"],
        )
