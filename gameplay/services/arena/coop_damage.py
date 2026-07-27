from __future__ import annotations

from collections import defaultdict

from battle.report_events import iter_damage_events


def aggregate_event_damage(rounds: list[dict], *, boss_template_key: str) -> dict[int, dict[str, int]]:
    rows: dict[int, dict[str, int]] = defaultdict(lambda: {"total_damage": 0, "boss_damage": 0, "guard_damage": 0})
    for event in iter_damage_events(rounds):
        entry_id = event.get("actor_owner_entry_id")
        damage = int(event.get("applied_damage", event.get("damage")) or 0)
        if not entry_id or damage <= 0:
            continue
        bucket = rows[int(entry_id)]
        bucket["total_damage"] += damage
        if event.get("target_template_key") == boss_template_key or event.get("target_is_boss"):
            bucket["boss_damage"] += damage
        else:
            bucket["guard_damage"] += damage
    return rows
