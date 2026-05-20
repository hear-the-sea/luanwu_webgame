from __future__ import annotations

from typing import Any, Dict

from ...models import Manor, MissionTemplate
from .enemy_guest_configs import EnemyGuestConfig, normalize_enemy_guest_configs


def _normalize_enemy_technology_config(raw) -> Dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        normalized: Dict[str, object] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise AssertionError(f"invalid mission enemy technology key: {key!r}")
            key_str = key.strip()
            if not key_str:
                raise AssertionError(f"invalid mission enemy technology key: {key!r}")
            normalized[key_str] = value
        return normalized
    raise AssertionError(f"invalid mission enemy technology: {raw!r}")


def _normalize_mapping(raw) -> Dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        normalized: Dict[str, object] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise AssertionError(f"invalid mission mapping key: {key!r}")
            key_str = key.strip()
            if not key_str:
                raise AssertionError(f"invalid mission mapping key: {key!r}")
            normalized[key_str] = value
        return normalized
    raise AssertionError(f"invalid mission mapping payload: {raw!r}")


def _normalize_guest_configs(raw) -> list[EnemyGuestConfig]:
    return normalize_enemy_guest_configs(raw)


def _resolve_defender_max_squad(defender_setup: Dict[str, object]) -> int | None:
    if not defender_setup:
        return None
    guest_configs = _normalize_guest_configs(defender_setup.get("guest_keys"))
    return len(guest_configs) or None


def _normalize_troop_loadout(raw) -> Dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid mission troop loadout: {raw!r}")
    normalized: Dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise AssertionError(f"invalid mission troop loadout key: {key!r}")
        key_str = key.strip()
        if not key_str:
            raise AssertionError(f"invalid mission troop loadout key: {key!r}")
        if isinstance(value, bool):
            raise AssertionError(f"invalid mission troop loadout quantity: {value!r}")
        try:
            qty = int(value)
        except (TypeError, ValueError):
            raise AssertionError(f"invalid mission troop loadout quantity: {value!r}") from None
        if qty < 0:
            raise AssertionError(f"invalid mission troop loadout quantity: {value!r}")
        normalized[key_str] = qty
    return normalized


def _coerce_enemy_guest_level(config: Dict[str, object], default: int = 50) -> int:
    raw_level: Any = config.get("guest_level", default)
    if isinstance(raw_level, bool):
        raise AssertionError(f"invalid mission enemy guest level: {raw_level!r}")
    try:
        level = int(raw_level)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission enemy guest level: {raw_level!r}") from exc
    if level <= 0:
        raise AssertionError(f"invalid mission enemy guest level: {raw_level!r}")
    return level


def _normalize_guest_skills(config: Dict[str, object]) -> list[str] | None:
    raw = config.get("guest_skills")
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        raise AssertionError(f"invalid mission enemy guest skills: {raw!r}")
    skills: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise AssertionError(f"invalid mission enemy guest skills entry: {item!r}")
        key = item.strip()
        if not key:
            raise AssertionError(f"invalid mission enemy guest skills entry: {item!r}")
        skills.append(key)
    return skills or None


def generate_sync_battle_report(
    *,
    manor: Manor,
    mission: MissionTemplate,
    guests,
    loadout: Dict[str, int],
    defender_setup: Dict[str, object],
    travel_seconds: int,
    seed=None,
):
    """Sync battle report helper used when Celery is unavailable."""

    from battle.services import simulate_report

    normalized_loadout = _normalize_troop_loadout(loadout)

    if mission.is_defense:
        from battle.combatants_pkg import build_named_ai_guests
        from gameplay.services.technology import (
            build_player_battle_technology_payload,
            get_guest_stat_bonuses,
            resolve_enemy_tech_levels,
        )

        tech_conf = _normalize_enemy_technology_config(mission.enemy_technology)
        attacker_guest_level = _coerce_enemy_guest_level(tech_conf)
        attacker_guests = build_named_ai_guests(
            _normalize_guest_configs(mission.enemy_guests), level=attacker_guest_level
        )
        attacker_tech_levels = resolve_enemy_tech_levels(tech_conf)
        attacker_guest_bonuses = get_guest_stat_bonuses(tech_conf)
        attacker_guest_skills = _normalize_guest_skills(tech_conf)
        enemy_troops = _normalize_troop_loadout(mission.enemy_troops)
        return simulate_report(
            manor=manor,
            battle_type=mission.battle_type or "task",
            seed=seed,
            troop_loadout=enemy_troops,
            fill_default_troops=False,
            attacker_guests=attacker_guests,
            defender_setup={
                "troop_loadout": normalized_loadout,
                "technology": build_player_battle_technology_payload(manor),
            },
            defender_guests=guests,
            defender_manor=manor,
            defender_max_squad=len(guests) if guests else None,
            drop_table={},
            opponent_name=mission.name,
            travel_seconds=travel_seconds,
            send_message=False,
            auto_reward=False,
            drop_handler=None,
            max_squad=len(attacker_guests) if attacker_guests else None,
            apply_damage=False,
            use_lock=False,
            attacker_tech_levels=attacker_tech_levels,
            attacker_guest_bonuses=attacker_guest_bonuses or None,
            attacker_guest_skills=attacker_guest_skills,
            attacker_manor=None,
            validate_attacker_troop_capacity=False,
        )

    return simulate_report(
        manor=manor,
        battle_type=mission.battle_type or "task",
        seed=seed,
        troop_loadout=normalized_loadout,
        fill_default_troops=False,
        attacker_guests=guests,
        defender_setup=defender_setup,
        defender_manor=None,
        drop_table=_normalize_mapping(mission.drop_table),
        opponent_name=mission.name,
        travel_seconds=travel_seconds,
        send_message=False,
        auto_reward=False,
        drop_handler=None,
        defender_max_squad=_resolve_defender_max_squad(defender_setup),
        max_squad=getattr(manor, "max_squad_size", None),
        apply_damage=False,
        use_lock=False,
    )
