from __future__ import annotations

from collections import defaultdict


def aggregate_event_damage(rounds: list[dict], *, boss_template_key: str) -> dict[int, dict[str, int]]:
    rows: dict[int, dict[str, int]] = defaultdict(lambda: {"total_damage": 0, "boss_damage": 0, "guard_damage": 0})
    for battle_round in rounds or []:
        for event in battle_round.get("events", []) or []:
            entry_id = event.get("actor_owner_entry_id")
            damage = int(event.get("damage") or 0)
            if not entry_id or damage <= 0:
                continue
            bucket = rows[int(entry_id)]
            bucket["total_damage"] += damage
            if event.get("target_template_key") == boss_template_key or event.get("target_is_boss"):
                bucket["boss_damage"] += damage
            else:
                bucket["guard_damage"] += damage
    return rows
