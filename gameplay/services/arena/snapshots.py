from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.exceptions import InvalidBattleSnapshotError
from guests.guest_combat_stats import resolve_guest_combat_stats


class _EmptySkillSet:
    @staticmethod
    def all() -> list:
        return []


def _invalid_snapshot(field_name: str, _raw: Any) -> InvalidBattleSnapshotError:
    return InvalidBattleSnapshotError(
        "竞技场门客快照数据无效",
        snapshot_kind="arena_guest_snapshot",
        field_name=field_name,
    )


def _snapshot_text(snapshot: dict[str, Any], field_name: str, *, default: str | None = None) -> str:
    if field_name not in snapshot:
        if default is not None:
            return default
        raise _invalid_snapshot(field_name, None)
    raw = snapshot[field_name]
    if not isinstance(raw, str) or not raw.strip():
        raise _invalid_snapshot(field_name, raw)
    return raw.strip()


def _snapshot_int(
    snapshot: dict[str, Any],
    field_name: str,
    *,
    minimum: int,
    default: int | None = None,
) -> int:
    if field_name not in snapshot:
        if default is not None:
            return default
        raise _invalid_snapshot(field_name, None)
    raw = snapshot[field_name]
    if raw is None or isinstance(raw, bool):
        raise _invalid_snapshot(field_name, raw)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise _invalid_snapshot(field_name, raw) from exc
    if value < minimum:
        raise _invalid_snapshot(field_name, raw)
    return value


def _snapshot_skill_keys(snapshot: dict[str, Any]) -> list[str]:
    raw = snapshot.get("skill_keys")
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise _invalid_snapshot("skill_keys", raw)

    normalized: list[str] = []
    for key in raw:
        if not isinstance(key, str):
            raise _invalid_snapshot("skill_keys", key)
        if key.strip():
            normalized.append(key.strip())
    return normalized


def normalize_entry_guest_snapshot(snapshot: Any) -> dict[str, Any]:
    """Validate a persisted arena snapshot without inventing combat stats."""

    if not isinstance(snapshot, dict) or not snapshot:
        raise _invalid_snapshot("snapshot", snapshot)

    normalized = dict(snapshot)
    normalized["display_name"] = _snapshot_text(snapshot, "display_name")
    normalized["rarity"] = _snapshot_text(snapshot, "rarity", default="gray")
    normalized["template_key"] = _snapshot_text(snapshot, "template_key")
    normalized["level"] = _snapshot_int(snapshot, "level", minimum=1, default=1)
    normalized["force"] = _snapshot_int(snapshot, "force", minimum=0, default=0)
    normalized["intellect"] = _snapshot_int(snapshot, "intellect", minimum=0, default=0)
    normalized["defense_stat"] = _snapshot_int(snapshot, "defense_stat", minimum=0, default=0)
    normalized["agility"] = _snapshot_int(snapshot, "agility", minimum=0, default=0)
    if "agility" not in snapshot:
        normalized["arena_power_snapshot_semantics"] = "legacy_missing_agility"
    normalized["luck"] = _snapshot_int(snapshot, "luck", minimum=0, default=0)
    normalized["attack"] = _snapshot_int(snapshot, "attack", minimum=1)
    normalized["defense"] = _snapshot_int(snapshot, "defense", minimum=1)
    normalized["max_hp"] = _snapshot_int(snapshot, "max_hp", minimum=1)
    normalized["current_hp"] = _snapshot_int(snapshot, "current_hp", minimum=1, default=1)
    normalized["skill_keys"] = _snapshot_skill_keys(snapshot)
    return normalized


class ArenaGuestSnapshotProxy:
    """竞技场报名快照，只保留战斗构建所需显式字段。"""

    def __init__(self, snapshot: dict[str, Any]):
        snapshot = normalize_entry_guest_snapshot(snapshot)
        self.pk = None
        self.id = None
        self.template = SimpleNamespace(
            key=snapshot["template_key"],
            initial_skills=_EmptySkillSet(),
        )
        self._display_name = snapshot["display_name"]
        self._rarity = snapshot["rarity"]
        self.level = snapshot["level"]
        self.force = snapshot["force"]
        self.intellect = snapshot["intellect"]
        self.defense_stat = snapshot["defense_stat"]
        self.agility = snapshot["agility"]
        self.luck = snapshot["luck"]
        self.current_hp = snapshot["current_hp"]
        self.attack = snapshot["attack"]
        self.defense = snapshot["defense"]
        self.max_hp = snapshot["max_hp"]
        self._override_skills = snapshot["skill_keys"]

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def rarity(self) -> str:
        return self._rarity


def serialize_guest_skill_keys(guest) -> list[str]:
    skills = getattr(guest, "skills", None)
    values_list = getattr(skills, "values_list", None)
    if callable(values_list):
        return [str(key).strip() for key in values_list("key", flat=True) if str(key).strip()]
    return [str(key).strip() for key in (getattr(guest, "_override_skills", None) or []) if str(key).strip()]


def build_entry_guest_snapshot(guest) -> dict[str, Any]:
    stats = resolve_guest_combat_stats(guest)
    current_hp = int(getattr(guest, "current_hp", 0) or 0)
    current_hp = min(stats.max_hp, max(1, current_hp if current_hp > 0 else stats.max_hp))
    return normalize_entry_guest_snapshot(
        {
            "snapshot_version": 1,
            "display_name": guest.display_name,
            "rarity": guest.rarity,
            "template_key": guest.template.key,
            "level": int(guest.level),
            "force": int(guest.force),
            "intellect": int(guest.intellect),
            "defense_stat": int(guest.defense_stat),
            "agility": int(guest.agility),
            "luck": int(guest.luck),
            "attack": stats.attack,
            "defense": stats.defense,
            "max_hp": stats.max_hp,
            "current_hp": current_hp,
            "skill_keys": serialize_guest_skill_keys(guest),
        }
    )


def load_entry_guests(entry, *, max_guests_per_entry: int = 10) -> list[ArenaGuestSnapshotProxy]:
    proxies: list[ArenaGuestSnapshotProxy] = []
    links = list(entry.entry_guests.order_by("created_at", "id")[: max(1, int(max_guests_per_entry))])
    for link in links:
        raw_snapshot = link.snapshot
        if raw_snapshot is None:
            snapshot: Any = {}
        elif isinstance(raw_snapshot, dict):
            snapshot = dict(raw_snapshot)
        else:
            raise _invalid_snapshot("snapshot", raw_snapshot)
        if not snapshot and getattr(link, "guest", None):
            snapshot = build_entry_guest_snapshot(link.guest)
        if not snapshot:
            continue
        proxies.append(ArenaGuestSnapshotProxy(snapshot))
    return proxies
