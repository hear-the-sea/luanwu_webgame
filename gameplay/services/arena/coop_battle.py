from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from battle.arena_coop import ARENA_COOP_ENEMY_FINAL_STATS, configure_arena_coop_enemy_guest
from battle.combatants_pkg import build_named_ai_guests
from battle.models import BattleReport
from battle.services import simulate_report
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent
from guests.models import Guest

from .snapshots import load_entry_guests


def merge_mapping(target: dict[str, Any], updates: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(updates, dict):
        return target
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_mapping(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def load_runtime_rules_for_event(base_rules: dict[str, Any], event: ArenaCoopEvent) -> dict[str, Any]:
    rules = copy.deepcopy(base_rules)
    merge_mapping(rules["enemy"], event.enemy_snapshot if isinstance(event.enemy_snapshot, dict) else {})
    reward_snapshot = event.reward_snapshot if isinstance(event.reward_snapshot, dict) else {}
    merge_mapping(rules["rewards"], reward_snapshot.get("rewards"))
    merge_mapping(rules["rare_drop"], reward_snapshot.get("rare_drop"))
    daily_snapshot = event.daily_rule_snapshot if isinstance(event.daily_rule_snapshot, dict) else {}
    merge_mapping(rules["registration"], daily_snapshot.get("registration"))
    merge_mapping(rules["contribution"], daily_snapshot.get("contribution"))
    return rules


def resolve_boss_initial_hp(boss_template_key: str) -> int:
    profile = ARENA_COOP_ENEMY_FINAL_STATS.get(str(boss_template_key or "").strip(), {})
    return max(0, int(profile.get("final_hp", 0) or 0))


def extract_boss_hp_snapshot(report: BattleReport, *, boss_template_key: str) -> tuple[int, int] | None:
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


def apply_combatant_metadata(guest: Any, *, owner_entry_id: int | None, combatant_slot: int, is_boss: bool) -> None:
    setattr(guest, "_owner_entry_id", owner_entry_id)
    setattr(guest, "_combatant_slot", combatant_slot)
    setattr(guest, "_is_boss", is_boss)


def build_attacker_guest_pool(registered_entries: list[ArenaCoopEntry], *, guest_limit_per_entry: int) -> list[Any]:
    attacker_guests: list[Any] = []
    for entry in registered_entries:
        for slot_index, guest in enumerate(load_entry_guests(entry, max_guests_per_entry=guest_limit_per_entry)):
            apply_combatant_metadata(guest, owner_entry_id=entry.id, combatant_slot=slot_index, is_boss=False)
            attacker_guests.append(guest)
    return attacker_guests


def build_defender_guest_pool(locked_event: ArenaCoopEvent) -> list[Guest]:
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
        apply_combatant_metadata(guest, owner_entry_id=None, combatant_slot=slot_index, is_boss=slot_index == 0)
    return defender_guests


def run_coop_battle_locked(locked_event: ArenaCoopEvent, now: datetime) -> BattleReport:
    registered_entries = list(
        locked_event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
        .select_related("manor")
        .order_by("joined_at", "id")
    )
    attacker_guests = build_attacker_guest_pool(
        registered_entries,
        guest_limit_per_entry=locked_event.guest_limit_per_entry,
    )
    defender_guests = build_defender_guest_pool(locked_event)
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
