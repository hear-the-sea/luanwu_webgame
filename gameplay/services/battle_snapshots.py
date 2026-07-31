from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite
from types import SimpleNamespace
from typing import Any

from django.utils import timezone

from battle.combatants_pkg.troop_device_bonuses import (
    ALLOWED_STATS,
    ALLOWED_TROOP_CLASSES,
    build_troop_device_bonuses_by_guest,
)
from core.exceptions import InvalidBattleSnapshotError
from guests.guest_combat_stats import resolve_guest_combat_stats
from guests.guest_rules import compute_guest_troop_capacity
from guests.models import Guest


class _EmptySkillSet:
    @staticmethod
    def all() -> list:
        return []


def _invalid_guest_snapshot(field_name: str) -> InvalidBattleSnapshotError:
    return InvalidBattleSnapshotError(
        "门客战斗快照数据无效",
        field_name=field_name,
    )


def _invalid_troop_loadout() -> InvalidBattleSnapshotError:
    return InvalidBattleSnapshotError(
        "护院编队快照数据无效",
        snapshot_kind="troop_loadout",
        field_name="troop_loadout",
    )


def _resolve_snapshot_text_field(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _invalid_guest_snapshot(field_name)
    return raw.strip()


def _resolve_snapshot_identity_int(raw: Any, *, field_name: str, required: bool) -> int | None:
    if raw is None:
        if required:
            raise _invalid_guest_snapshot(field_name)
        return None
    if isinstance(raw, bool):
        raise _invalid_guest_snapshot(field_name)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise _invalid_guest_snapshot(field_name) from exc
    if value <= 0:
        raise _invalid_guest_snapshot(field_name)
    return value


def _resolve_snapshot_stat_int(raw: Any, *, field_name: str, minimum: int = 0) -> int:
    if raw is None or isinstance(raw, bool):
        raise _invalid_guest_snapshot(field_name)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise _invalid_guest_snapshot(field_name) from exc
    if value < minimum:
        raise _invalid_guest_snapshot(field_name)
    return value


def _resolve_snapshot_nonnegative_number(raw: Any, *, field_name: str) -> int | float:
    if raw is None or isinstance(raw, bool):
        raise _invalid_guest_snapshot(field_name)
    try:
        value = float(raw)
    except (OverflowError, TypeError, ValueError) as exc:
        raise _invalid_guest_snapshot(field_name) from exc
    if not isfinite(value) or value < 0:
        raise _invalid_guest_snapshot(field_name)
    return int(value) if value.is_integer() else value


def _resolve_snapshot_template_key(snapshot: dict[str, Any]) -> str:
    raw_key = snapshot.get("template_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise _invalid_guest_snapshot("template_key")
    return raw_key.strip()


def _normalize_snapshot_skill_keys(snapshot: dict[str, Any]) -> list[str]:
    raw_skill_keys = snapshot.get("skill_keys")
    if raw_skill_keys is None:
        return []
    if not isinstance(raw_skill_keys, (list, tuple, set)):
        raise _invalid_guest_snapshot("skill_keys")
    normalized: list[str] = []
    for key in raw_skill_keys:
        if not isinstance(key, str) or not key.strip():
            raise _invalid_guest_snapshot("skill_keys")
        normalized.append(key.strip())
    return normalized


def _normalize_snapshot_troop_device_bonuses(snapshot: dict[str, Any]) -> dict[str, dict[str, dict[str, int | float]]]:
    raw_bonuses = snapshot.get("troop_device_bonuses")
    if raw_bonuses is None:
        return {}
    if not isinstance(raw_bonuses, Mapping):
        raise _invalid_guest_snapshot("troop_device_bonuses")

    normalized: dict[str, dict[str, dict[str, int | float]]] = {}
    for troop_class, raw_stats in raw_bonuses.items():
        if troop_class not in ALLOWED_TROOP_CLASSES or not isinstance(raw_stats, Mapping):
            raise _invalid_guest_snapshot("troop_device_bonuses")

        for stat, raw_bonus in raw_stats.items():
            if stat not in ALLOWED_STATS or not isinstance(raw_bonus, Mapping):
                raise _invalid_guest_snapshot("troop_device_bonuses")
            if any(key not in {"flat", "pct"} for key in raw_bonus):
                raise _invalid_guest_snapshot("troop_device_bonuses")

            flat_value = _resolve_snapshot_nonnegative_number(
                raw_bonus.get("flat", 0),
                field_name="troop_device_bonuses",
            )
            pct_value = _resolve_snapshot_nonnegative_number(
                raw_bonus.get("pct", 0),
                field_name="troop_device_bonuses",
            )
            if flat_value <= 0 and pct_value <= 0:
                continue
            normalized.setdefault(troop_class, {})[stat] = {
                "flat": flat_value,
                "pct": pct_value,
            }
    return normalized


def _normalize_serialized_skill_keys(raw_skill_keys: Any, *, field_name: str) -> list[str]:
    if raw_skill_keys is None:
        return []
    if isinstance(raw_skill_keys, (str, bytes)) or not isinstance(raw_skill_keys, Iterable):
        raise AssertionError(f"invalid battle guest {field_name}: {raw_skill_keys!r}")
    normalized: list[str] = []
    for key in raw_skill_keys:
        if not isinstance(key, str) or not key.strip():
            raise AssertionError(f"invalid battle guest {field_name} entry: {key!r}")
        normalized.append(key.strip())
    return normalized


def _resolve_live_snapshot_identity(guest: Any, *, field_name: str) -> int:
    raw = getattr(guest, field_name, None)
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}") from exc
    if value <= 0:
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    return value


def _resolve_live_snapshot_template_key(guest: Any) -> str:
    raw = getattr(getattr(guest, "template", None), "key", None)
    if not isinstance(raw, str) or not raw.strip():
        raise AssertionError(f"invalid battle guest template.key: {raw!r}")
    return raw.strip()


def _resolve_live_snapshot_text_field(guest: Any, *, field_name: str) -> str:
    raw = getattr(guest, field_name, None)
    if not isinstance(raw, str) or not raw.strip():
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    return raw.strip()


def _resolve_live_snapshot_stat_int(guest: Any, *, field_name: str, minimum: int = 0) -> int:
    raw = getattr(guest, field_name, None)
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}") from exc
    if value < minimum:
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    return value


def _resolve_live_computed_stat_int(raw: Any, *, field_name: str, minimum: int = 0) -> int:
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}") from exc
    if value < minimum:
        raise AssertionError(f"invalid battle guest {field_name}: {raw!r}")
    return value


class BattleGuestSnapshotProxy:
    """门客战斗快照，只保留 battle 构建链路需要的显式字段。"""

    def __init__(self, snapshot: dict[str, Any], *, include_guest_identity: bool = False):
        now = timezone.now()
        guest_id = (
            _resolve_snapshot_identity_int(snapshot.get("guest_id"), field_name="guest_id", required=True)
            if include_guest_identity
            else None
        )
        guest_manor_id = (
            _resolve_snapshot_identity_int(snapshot.get("manor_id"), field_name="manor_id", required=False)
            if include_guest_identity
            else None
        )
        self.pk = guest_id
        self.id = guest_id
        self.manor_id = guest_manor_id
        self.is_battle_snapshot_proxy = True
        self.template = SimpleNamespace(
            key=_resolve_snapshot_template_key(snapshot),
            initial_skills=_EmptySkillSet(),
        )
        self._display_name = _resolve_snapshot_text_field(snapshot.get("display_name"), field_name="display_name")
        self._rarity = _resolve_snapshot_text_field(snapshot.get("rarity"), field_name="rarity")
        self.level = _resolve_snapshot_stat_int(snapshot.get("level"), field_name="level", minimum=1)
        self.force = _resolve_snapshot_stat_int(snapshot.get("force"), field_name="force", minimum=0)
        self.intellect = _resolve_snapshot_stat_int(snapshot.get("intellect"), field_name="intellect", minimum=0)
        self.defense_stat = _resolve_snapshot_stat_int(
            snapshot.get("defense_stat"), field_name="defense_stat", minimum=0
        )
        self.agility = _resolve_snapshot_stat_int(snapshot.get("agility"), field_name="agility", minimum=0)
        self.luck = _resolve_snapshot_stat_int(snapshot.get("luck"), field_name="luck", minimum=0)
        self.current_hp = _resolve_snapshot_stat_int(snapshot.get("current_hp"), field_name="current_hp", minimum=1)
        self.status = _resolve_snapshot_text_field(snapshot.get("status"), field_name="status")
        self.last_hp_recovery_at = now
        self.created_at = now
        self.attack = _resolve_snapshot_stat_int(snapshot.get("attack"), field_name="attack", minimum=1)
        self.defense = _resolve_snapshot_stat_int(snapshot.get("defense"), field_name="defense", minimum=1)
        self.max_hp = _resolve_snapshot_stat_int(snapshot.get("max_hp"), field_name="max_hp", minimum=1)
        self.troop_capacity = _resolve_snapshot_stat_int(
            snapshot.get("troop_capacity"), field_name="troop_capacity", minimum=0
        )
        self.troop_device_bonuses = _normalize_snapshot_troop_device_bonuses(snapshot)
        self.manor = None
        self._override_skills = _normalize_snapshot_skill_keys(snapshot)

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def rarity(self) -> str:
        return self._rarity


def _serialize_guest_skill_keys(guest: Any) -> list[str]:
    skills = getattr(guest, "skills", None)
    values_list = getattr(skills, "values_list", None)
    if callable(values_list):
        return _normalize_serialized_skill_keys(values_list("key", flat=True), field_name="skill_keys")
    return _normalize_serialized_skill_keys(getattr(guest, "_override_skills", None), field_name="override skill_keys")


def build_guest_battle_snapshot(
    guest: Any,
    *,
    include_identity: bool = True,
    troop_device_bonuses: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stats = resolve_guest_combat_stats(guest)
    attack = _resolve_live_computed_stat_int(stats.attack, field_name="attack", minimum=1)
    defense = _resolve_live_computed_stat_int(stats.defense, field_name="defense", minimum=1)
    max_hp = _resolve_live_computed_stat_int(stats.max_hp, field_name="max_hp", minimum=1)
    troop_capacity_raw = compute_guest_troop_capacity(guest) if isinstance(guest, Guest) else stats.troop_capacity
    troop_capacity = _resolve_live_computed_stat_int(troop_capacity_raw, field_name="troop_capacity", minimum=0)
    current_hp = _resolve_live_snapshot_stat_int(guest, field_name="current_hp", minimum=1)
    current_hp = min(max_hp, current_hp)
    if troop_device_bonuses is None:
        troop_device_bonuses = build_troop_device_bonuses_by_guest([guest])[0]
    payload: dict[str, Any] = {
        "snapshot_version": 1,
        "display_name": _resolve_live_snapshot_text_field(guest, field_name="display_name"),
        "rarity": _resolve_live_snapshot_text_field(guest, field_name="rarity"),
        "status": _resolve_live_snapshot_text_field(guest, field_name="status"),
        "template_key": _resolve_live_snapshot_template_key(guest),
        "level": _resolve_live_snapshot_stat_int(guest, field_name="level", minimum=1),
        "force": _resolve_live_snapshot_stat_int(guest, field_name="force", minimum=0),
        "intellect": _resolve_live_snapshot_stat_int(guest, field_name="intellect", minimum=0),
        "defense_stat": _resolve_live_snapshot_stat_int(guest, field_name="defense_stat", minimum=0),
        "agility": _resolve_live_snapshot_stat_int(guest, field_name="agility", minimum=0),
        "luck": _resolve_live_snapshot_stat_int(guest, field_name="luck", minimum=0),
        "attack": attack,
        "defense": defense,
        "max_hp": max_hp,
        "current_hp": current_hp,
        "troop_capacity": troop_capacity,
        "troop_device_bonuses": dict(troop_device_bonuses),
        "skill_keys": _serialize_guest_skill_keys(guest),
    }
    if include_identity:
        payload["guest_id"] = _resolve_live_snapshot_identity(guest, field_name="id")
        payload["manor_id"] = _resolve_live_snapshot_identity(guest, field_name="manor_id")
    return payload


def build_guest_battle_snapshots(
    guests: Iterable[Guest],
    *,
    include_identity: bool = True,
) -> list[dict[str, Any]]:
    guest_list = list(guests)
    device_bonuses_by_guest = build_troop_device_bonuses_by_guest(guest_list)
    return [
        build_guest_battle_snapshot(
            guest,
            include_identity=include_identity,
            troop_device_bonuses=device_bonuses_by_guest[index],
        )
        for index, guest in enumerate(guest_list)
    ]


def build_guest_snapshot_proxies(
    snapshots: Any,
    *,
    include_guest_identity: bool = False,
) -> list[BattleGuestSnapshotProxy]:
    if not isinstance(snapshots, list):
        raise _invalid_guest_snapshot("guest_snapshots")
    proxies: list[BattleGuestSnapshotProxy] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not snapshot:
            raise _invalid_guest_snapshot("guest_snapshots")
        payload = dict(snapshot)
        proxies.append(BattleGuestSnapshotProxy(payload, include_guest_identity=include_guest_identity))
    return proxies


def validate_battle_troop_loadout(raw: Any) -> dict[str, int]:
    """Validate a persisted troop loadout without silently discarding corrupt entries."""

    if not isinstance(raw, dict):
        raise _invalid_troop_loadout()

    normalized: dict[str, int] = {}
    for raw_key, raw_value in raw.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise _invalid_troop_loadout()
        key = raw_key.strip()
        if key in normalized:
            raise _invalid_troop_loadout()
        if raw_value is None or isinstance(raw_value, bool):
            raise _invalid_troop_loadout()
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise _invalid_troop_loadout() from exc
        if value <= 0:
            raise _invalid_troop_loadout()
        normalized[key] = value
    return normalized
