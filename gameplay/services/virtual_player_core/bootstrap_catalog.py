from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from typing import Any

from django.db import transaction

from battle.models import TroopTemplate
from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys
from gameplay.models import BuildingType, ItemTemplate
from gameplay.services.technology_catalog import build_technology_index, build_troop_to_class_index
from guests.models import GearTemplate, GuestTemplate, RecruitmentPoolEntry, Skill

from .random_context import canonical_json_bytes
from .skill_policy import is_virtual_player_skill_allowed

ALL_TEMPLATE_SENTINEL = "__all__"
ALL_TRADEABLE_TEMPLATE_SENTINEL = "__all_tradeable__"
CORE_BUILDING_KEYS = (
    BuildingKeys.SILVER_VAULT,
    BuildingKeys.GRANARY,
    BuildingKeys.JUXIAN_ZHUANG,
    BuildingKeys.JIADING_FANG,
    BuildingKeys.YOUXIA_BAOTA,
    BuildingKeys.LIANGGONG_CHANG,
)


class BootstrapCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BuildingCatalogEntry:
    key: str
    max_level: int | None


@dataclass(frozen=True, slots=True)
class TechnologyCatalogEntry:
    key: str
    max_level: int
    troop_class: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class GuestCatalogEntry:
    key: str
    archetype: str
    rarity: str
    recruitable: bool
    is_hermit: bool
    base_attack: int
    base_intellect: int
    base_defense: int
    base_agility: int
    base_luck: int
    base_hp: int
    default_gender: str
    default_morality: int
    initial_skill_keys: tuple[str, ...]
    recruitment_weight: int


@dataclass(frozen=True, slots=True)
class GearCatalogEntry:
    key: str
    slot: str
    rarity: str
    set_key: str
    attack_bonus: int
    defense_bonus: int
    extra_stats_digest: str
    set_bonus_digest: str


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    key: str
    kind: str
    rarity: str
    required_level: int
    required_force: int
    required_intellect: int
    required_defense: int
    required_agility: int


@dataclass(frozen=True, slots=True)
class TroopCatalogEntry:
    key: str
    troop_class: str
    base_attack: int
    base_defense: int
    base_hp: int
    speed_bonus: int


@dataclass(frozen=True, slots=True)
class InventoryCatalogEntry:
    key: str
    effect_type: str
    rarity: str
    tradeable: bool
    price: int
    storage_space: int
    effect_payload_digest: str


@dataclass(frozen=True, slots=True)
class BootstrapCatalog:
    buildings: tuple[BuildingCatalogEntry, ...]
    technologies: tuple[TechnologyCatalogEntry, ...]
    guests: tuple[GuestCatalogEntry, ...]
    gear: tuple[GearCatalogEntry, ...]
    skills: tuple[SkillCatalogEntry, ...]
    troops: tuple[TroopCatalogEntry, ...]
    inventory: tuple[InventoryCatalogEntry, ...]
    digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "buildings",
            "technologies",
            "guests",
            "gear",
            "skills",
            "troops",
            "inventory",
        ):
            values = tuple(getattr(self, field_name))
            keys = tuple(value.key for value in values)
            if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
                raise BootstrapCatalogError(f"{field_name} catalog keys must be sorted and unique")
            object.__setattr__(self, field_name, values)
        if len(self.digest) != 64 or any(character not in "0123456789abcdef" for character in self.digest):
            raise BootstrapCatalogError("catalog digest must be a lowercase SHA-256")


def _queryset_for_lock(queryset, *, lock: bool):
    if not lock:
        return queryset
    if not transaction.get_connection().in_atomic_block:
        raise BootstrapCatalogError("locked catalog reads require transaction.atomic()")
    return queryset.select_for_update()


def _configured_values(config: Mapping[str, Any], field: str) -> tuple[str, ...]:
    projection = config.get("projection") or {}
    if not isinstance(projection, Mapping):
        raise BootstrapCatalogError("virtual-player projection config must be a mapping")
    raw = projection.get(field) or ()
    if isinstance(raw, str):
        values: Sequence[object] = (raw,)
    elif isinstance(raw, Sequence):
        values = raw
    else:
        raise BootstrapCatalogError(f"projection.{field} must be a string or sequence")
    normalized = tuple(str(value).strip() for value in values if str(value).strip())
    if len(normalized) != len(set(normalized)):
        raise BootstrapCatalogError(f"projection.{field} contains duplicate keys")
    return normalized


def _selected_keys(
    *,
    configured: tuple[str, ...],
    available: set[str],
    all_sentinels: frozenset[str],
    field: str,
) -> set[str]:
    if set(configured) & set(all_sentinels):
        unknown_sentinels = set(configured) & {
            ALL_TEMPLATE_SENTINEL,
            ALL_TRADEABLE_TEMPLATE_SENTINEL,
        } - set(all_sentinels)
        if unknown_sentinels:
            raise BootstrapCatalogError(f"projection.{field} contains an unsupported catalog sentinel")
        return set(available)
    missing = sorted(set(configured) - available)
    if missing:
        raise BootstrapCatalogError(f"projection.{field} references missing templates: {', '.join(missing)}")
    return set(configured)


def _payload_digest(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _catalog_payload(
    *,
    buildings: tuple[BuildingCatalogEntry, ...],
    technologies: tuple[TechnologyCatalogEntry, ...],
    guests: tuple[GuestCatalogEntry, ...],
    gear: tuple[GearCatalogEntry, ...],
    skills: tuple[SkillCatalogEntry, ...],
    troops: tuple[TroopCatalogEntry, ...],
    inventory: tuple[InventoryCatalogEntry, ...],
) -> dict[str, object]:
    def rows(values: Sequence[object]) -> list[dict[str, object]]:
        return [
            {field: getattr(value, field) for field in value.__dataclass_fields__}  # type: ignore[attr-defined]
            for value in values
        ]

    return {
        "schema_version": 1,
        "buildings": rows(buildings),
        "technologies": rows(technologies),
        "guests": rows(guests),
        "gear": rows(gear),
        "skills": rows(skills),
        "troops": rows(troops),
        "inventory": rows(inventory),
    }


def _load_bootstrap_catalog_uncached(
    config: Mapping[str, Any],
    *,
    lock: bool = False,
) -> BootstrapCatalog:
    """Load one immutable, read-only template snapshot for V2 bootstrap."""
    building_rows = list(_queryset_for_lock(BuildingType.objects.order_by("key"), lock=lock).values("key"))
    buildings = tuple(
        BuildingCatalogEntry(
            key=str(row["key"]),
            max_level=(int(BUILDING_MAX_LEVELS[str(row["key"])]) if str(row["key"]) in BUILDING_MAX_LEVELS else None),
        )
        for row in building_rows
    )
    missing_core_buildings = sorted(set(CORE_BUILDING_KEYS) - {entry.key for entry in buildings})
    if missing_core_buildings:
        raise BootstrapCatalogError("bootstrap catalog is missing core buildings: " + ", ".join(missing_core_buildings))

    technology_index = build_technology_index()
    configured_technology_keys = _configured_values(config, "technology_keys")
    selected_technology_keys = _selected_keys(
        configured=configured_technology_keys,
        available=set(technology_index),
        all_sentinels=frozenset({ALL_TEMPLATE_SENTINEL}),
        field="technology_keys",
    )
    technologies = tuple(
        TechnologyCatalogEntry(
            key=key,
            max_level=max(0, int(technology_index[key].get("max_level", 0) or 0)),
            troop_class=str(technology_index[key].get("troop_class") or ""),
            payload_digest=_payload_digest(technology_index[key]),
        )
        for key in sorted(selected_technology_keys)
    )

    skill_rows = list(
        _queryset_for_lock(Skill.objects.order_by("key"), lock=lock).values(
            "id",
            "key",
            "kind",
            "rarity",
            "required_level",
            "required_force",
            "required_intellect",
            "required_defense",
            "required_agility",
        )
    )
    skill_key_by_id = {int(row["id"]): str(row["key"]) for row in skill_rows}
    skills = tuple(
        SkillCatalogEntry(
            key=str(row["key"]),
            kind=str(row["kind"]),
            rarity=str(row["rarity"]),
            required_level=int(row["required_level"] or 0),
            required_force=int(row["required_force"] or 0),
            required_intellect=int(row["required_intellect"] or 0),
            required_defense=int(row["required_defense"] or 0),
            required_agility=int(row["required_agility"] or 0),
        )
        for row in skill_rows
        if is_virtual_player_skill_allowed(str(row["key"]))
    )

    guest_rows = list(
        _queryset_for_lock(GuestTemplate.objects.order_by("key"), lock=lock).values(
            "id",
            "key",
            "archetype",
            "rarity",
            "recruitable",
            "is_hermit",
            "base_attack",
            "base_intellect",
            "base_defense",
            "base_agility",
            "base_luck",
            "base_hp",
            "default_gender",
            "default_morality",
        )
    )
    available_guest_keys = {str(row["key"]) for row in guest_rows}
    selected_guest_keys = _selected_keys(
        configured=_configured_values(config, "guest_template_keys"),
        available=available_guest_keys,
        all_sentinels=frozenset({ALL_TEMPLATE_SENTINEL}),
        field="guest_template_keys",
    )
    selected_guest_rows = [
        row for row in guest_rows if str(row["key"]) in selected_guest_keys and bool(row["recruitable"])
    ]
    if not selected_guest_rows:
        raise BootstrapCatalogError("bootstrap catalog has no recruitable guest templates")
    selected_guest_ids = {int(row["id"]) for row in selected_guest_rows}
    through = GuestTemplate.initial_skills.through
    skill_links = list(
        _queryset_for_lock(
            through.objects.filter(guesttemplate_id__in=selected_guest_ids),  # type: ignore[misc]
            lock=lock,
        ).values_list("guesttemplate_id", "skill_id")
    )
    initial_skills_by_guest: dict[int, list[str]] = {}
    for guest_id, skill_id in skill_links:
        skill_key = skill_key_by_id.get(int(skill_id))
        if skill_key is None:
            raise BootstrapCatalogError("guest template references a missing skill")
        initial_skills_by_guest.setdefault(int(guest_id), []).append(skill_key)

    recruitment_rows = list(
        _queryset_for_lock(
            RecruitmentPoolEntry.objects.order_by("id"),
            lock=lock,
        ).values("template_id", "rarity", "archetype", "weight")
    )

    def recruitment_weight(row: Mapping[str, object]) -> int:
        total = 0
        for entry in recruitment_rows:
            template_id = entry["template_id"]
            if template_id is not None and int(str(template_id)) != int(str(row["id"])):
                continue
            if template_id is None:
                rarity = str(entry["rarity"] or "")
                archetype = str(entry["archetype"] or "")
                if rarity and rarity != str(row["rarity"]):
                    continue
                if archetype and archetype != str(row["archetype"]):
                    continue
            total += max(0, int(entry["weight"] or 0))
        return max(1, total)

    guests = tuple(
        GuestCatalogEntry(
            key=str(row["key"]),
            archetype=str(row["archetype"]),
            rarity=str(row["rarity"]),
            recruitable=bool(row["recruitable"]),
            is_hermit=bool(row["is_hermit"]),
            base_attack=int(row["base_attack"] or 0),
            base_intellect=int(row["base_intellect"] or 0),
            base_defense=int(row["base_defense"] or 0),
            base_agility=int(row["base_agility"] or 0),
            base_luck=int(row["base_luck"] or 0),
            base_hp=int(row["base_hp"] or 0),
            default_gender=str(row["default_gender"] or "unknown"),
            default_morality=int(row["default_morality"] or 0),
            initial_skill_keys=tuple(sorted(initial_skills_by_guest.get(int(row["id"]), ()))),
            recruitment_weight=recruitment_weight(row),
        )
        for row in selected_guest_rows
    )

    gear_rows = list(
        _queryset_for_lock(GearTemplate.objects.order_by("key"), lock=lock).values(
            "key",
            "slot",
            "rarity",
            "set_key",
            "attack_bonus",
            "defense_bonus",
            "extra_stats",
            "set_bonus",
        )
    )
    available_gear_keys = {str(row["key"]) for row in gear_rows}
    selected_gear_keys = _selected_keys(
        configured=_configured_values(config, "gear_template_keys"),
        available=available_gear_keys,
        all_sentinels=frozenset({ALL_TEMPLATE_SENTINEL}),
        field="gear_template_keys",
    )
    gear = tuple(
        GearCatalogEntry(
            key=str(row["key"]),
            slot=str(row["slot"]),
            rarity=str(row["rarity"]),
            set_key=str(row["set_key"] or ""),
            attack_bonus=int(row["attack_bonus"] or 0),
            defense_bonus=int(row["defense_bonus"] or 0),
            extra_stats_digest=_payload_digest(row["extra_stats"] or {}),
            set_bonus_digest=_payload_digest(row["set_bonus"] or {}),
        )
        for row in gear_rows
        if str(row["key"]) in selected_gear_keys
    )

    troop_rows = list(
        _queryset_for_lock(TroopTemplate.objects.order_by("key"), lock=lock).values(
            "key",
            "base_attack",
            "base_defense",
            "base_hp",
            "speed_bonus",
        )
    )
    available_troop_keys = {str(row["key"]) for row in troop_rows}
    selected_troop_keys = _selected_keys(
        configured=_configured_values(config, "troop_template_keys"),
        available=available_troop_keys,
        all_sentinels=frozenset({ALL_TEMPLATE_SENTINEL}),
        field="troop_template_keys",
    )
    troop_to_class = build_troop_to_class_index()
    troops = tuple(
        TroopCatalogEntry(
            key=str(row["key"]),
            troop_class=str(troop_to_class.get(str(row["key"]), "")),
            base_attack=int(row["base_attack"] or 0),
            base_defense=int(row["base_defense"] or 0),
            base_hp=int(row["base_hp"] or 0),
            speed_bonus=int(row["speed_bonus"] or 0),
        )
        for row in troop_rows
        if str(row["key"]) in selected_troop_keys
    )
    if selected_troop_keys and any(not entry.troop_class for entry in troops):
        missing_classes = sorted(entry.key for entry in troops if not entry.troop_class)
        raise BootstrapCatalogError("bootstrap troops are missing class mappings: " + ", ".join(missing_classes))

    item_rows = list(
        _queryset_for_lock(
            ItemTemplate.objects.filter(tradeable=True).order_by("key"),
            lock=lock,
        ).values(
            "key",
            "effect_type",
            "rarity",
            "tradeable",
            "price",
            "storage_space",
            "effect_payload",
        )
    )
    available_item_keys = {str(row["key"]) for row in item_rows}
    configured_item_keys = tuple(
        dict.fromkeys(
            (
                *_configured_values(config, "item_template_keys"),
                *_configured_values(config, "loot_item_template_keys"),
            )
        )
    )
    selected_item_keys = _selected_keys(
        configured=configured_item_keys,
        available=available_item_keys,
        all_sentinels=frozenset({ALL_TEMPLATE_SENTINEL, ALL_TRADEABLE_TEMPLATE_SENTINEL}),
        field="item_template_keys",
    )
    inventory = tuple(
        InventoryCatalogEntry(
            key=str(row["key"]),
            effect_type=str(row["effect_type"]),
            rarity=str(row["rarity"]),
            tradeable=bool(row["tradeable"]),
            price=int(row["price"] or 0),
            storage_space=max(1, int(row["storage_space"] or 1)),
            effect_payload_digest=_payload_digest(row["effect_payload"] or {}),
        )
        for row in item_rows
        if str(row["key"]) in selected_item_keys
    )

    payload = _catalog_payload(
        buildings=buildings,
        technologies=technologies,
        guests=guests,
        gear=gear,
        skills=skills,
        troops=troops,
        inventory=inventory,
    )
    return BootstrapCatalog(
        buildings=buildings,
        technologies=technologies,
        guests=guests,
        gear=gear,
        skills=skills,
        troops=troops,
        inventory=inventory,
        digest=sha256(canonical_json_bytes(payload)).hexdigest(),
    )


@lru_cache(maxsize=2)
def _load_bootstrap_catalog_cached(config_payload: bytes) -> BootstrapCatalog:
    config = json.loads(config_payload.decode("utf-8"))
    if not isinstance(config, Mapping):
        raise BootstrapCatalogError("cached bootstrap catalog config must be a mapping")
    return _load_bootstrap_catalog_uncached(config)


def load_bootstrap_catalog(
    config: Mapping[str, Any],
    *,
    lock: bool = False,
) -> BootstrapCatalog:
    """Load a catalog, caching only unlocked planning reads.

    Locked materialization reads always bypass the cache and revalidate the
    current catalog digest before writing assets.
    """
    if lock:
        return _load_bootstrap_catalog_uncached(config, lock=True)
    return _load_bootstrap_catalog_cached(canonical_json_bytes(config))


def clear_bootstrap_catalog_cache() -> None:
    _load_bootstrap_catalog_cached.cache_clear()


__all__ = [
    "BootstrapCatalog",
    "BootstrapCatalogError",
    "BuildingCatalogEntry",
    "GearCatalogEntry",
    "GuestCatalogEntry",
    "InventoryCatalogEntry",
    "SkillCatalogEntry",
    "TechnologyCatalogEntry",
    "TroopCatalogEntry",
    "clear_bootstrap_catalog_cache",
    "load_bootstrap_catalog",
]
