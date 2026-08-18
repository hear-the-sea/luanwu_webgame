from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction

from battle.models import TroopTemplate
from common.constants.virtual_players import VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS
from core.config import GUEST
from gameplay.constants import BuildingKeys
from gameplay.models import Building, BuildingType, InventoryItem, ItemTemplate, Manor, PlayerTechnology, PlayerTroop
from gameplay.models.manor import (
    GUEST_CAPACITY_BASE,
    GUEST_CAPACITY_PER_LEVEL,
    RETAINER_CAPACITY_BASE,
    RETAINER_CAPACITY_PER_LEVEL,
)
from gameplay.services.inventory.core import (
    GRAIN_ITEM_KEY,
    TREASURY_BLOCKED_ITEM_KEYS,
    add_item_to_inventory_locked,
    set_warehouse_grain_quantity_locked,
)
from gameplay.services.manor.core import calculate_building_capacity
from gameplay.services.manor.troop_capacity import MANOR_TROOP_CAPACITY
from guests.models import GearItem, GearTemplate, Guest, GuestSkill, GuestTemplate, Skill
from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest, slot_capacity
from guests.services.recruitment_guests import create_guest_from_template
from guests.utils.equipment_utils import SET_STAT_FIELD_MAP

from .bootstrap_assets import RARITY_RANK, _configured_max_rarity, guest_random, guest_seed_attributes
from .bootstrap_catalog import BootstrapCatalog
from .inventory_budget import apply_inventory_daily_caps
from .projection import BootstrapAssetTargets
from .random_context import RandomContext


class BootstrapMaterializationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializedBootstrapAssets:
    buildings: tuple[Building, ...]
    technologies: tuple[PlayerTechnology, ...]
    guests: tuple[Guest, ...]
    gear: tuple[GearItem, ...]
    skills: tuple[GuestSkill, ...]
    troops: tuple[PlayerTroop, ...]
    inventory: tuple[InventoryItem, ...]


def _require_atomic() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise BootstrapMaterializationError("V2 bootstrap materialization requires transaction.atomic()")


def _history_time(created_at: datetime, offset: int) -> datetime:
    return created_at + timedelta(days=int(offset))


def _gear_link_identity(row: GearItem) -> tuple[int, int]:
    if row.guest_id is None or row.template_id is None:
        raise BootstrapMaterializationError("materialized gear is missing its guest or template identity")
    return int(row.guest_id), int(row.template_id)


def _load_exact_templates(
    model,
    keys: set[str],
    *,
    label: str,
    allow_missing: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not keys:
        return {}
    values = {str(value.key): value for value in model.objects.filter(key__in=sorted(keys)).order_by("key")}
    missing = sorted(keys - set(values) - set(allow_missing))
    if missing:
        raise BootstrapMaterializationError(f"bootstrap {label} templates disappeared: {', '.join(missing)}")
    return values


def _materialize_buildings(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    account_created_at: datetime,
) -> tuple[Building, ...]:
    catalog_by_key = {entry.key: entry for entry in catalog.buildings}
    target_keys = set(assets.building_levels)
    if not target_keys <= set(catalog_by_key):
        raise BootstrapMaterializationError("blueprint references buildings outside the locked catalog")
    building_types = _load_exact_templates(
        BuildingType,
        target_keys,
        label="building",
    )
    existing_by_key = {
        str(row.building_type.key): row for row in Building.objects.filter(manor=manor).select_related("building_type")
    }
    missing_rows = [
        Building(manor=manor, building_type=building_types[key], level=1)
        for key in sorted(target_keys - set(existing_by_key))
    ]
    if missing_rows:
        Building.objects.bulk_create(missing_rows)
        existing_by_key = {
            str(row.building_type.key): row
            for row in Building.objects.filter(manor=manor).select_related("building_type")
        }
    if target_keys != set(existing_by_key):
        raise BootstrapMaterializationError("materialized building set does not match the blueprint")

    buildings: list[Building] = []
    for key in sorted(target_keys):
        building = existing_by_key[key]
        target_level = int(assets.building_levels[key])
        max_level = catalog_by_key[key].max_level
        if target_level < 1 or (max_level is not None and target_level > int(max_level)):
            raise BootstrapMaterializationError(f"bootstrap building level is outside the catalog limit: {key}")
        building.level = target_level
        building.is_upgrading = False
        building.upgrade_complete_at = None
        building.created_at = _history_time(
            account_created_at,
            int(assets.building_created_day_offsets[key]),
        )
        building.hp_updated_at = building.created_at
        buildings.append(building)
    Building.objects.bulk_update(
        buildings,
        [
            "level",
            "is_upgrading",
            "upgrade_complete_at",
            "created_at",
            "hp_updated_at",
        ],
    )
    manor.invalidate_building_cache()
    return tuple(buildings)


def _validate_manor_capacity_and_resources(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    now: datetime,
) -> int:
    silver_level = int(assets.building_levels[BuildingKeys.SILVER_VAULT])
    grain_level = int(assets.building_levels[BuildingKeys.GRANARY])
    manor.silver_capacity = calculate_building_capacity(
        silver_level,
        is_silver_vault=True,
    )
    manor.grain_capacity = calculate_building_capacity(
        grain_level,
        is_silver_vault=False,
    )
    if not 0 <= int(assets.silver) <= manor.silver_capacity:
        raise BootstrapMaterializationError("bootstrap silver exceeds the materialized vault capacity")
    if not 0 <= int(assets.grain) <= manor.grain_capacity:
        raise BootstrapMaterializationError("bootstrap grain exceeds the materialized granary capacity")

    guest_capacity = (
        GUEST_CAPACITY_BASE + int(assets.building_levels[BuildingKeys.JUXIAN_ZHUANG]) * GUEST_CAPACITY_PER_LEVEL
    )
    if len(assets.guests) > guest_capacity:
        raise BootstrapMaterializationError("bootstrap guest roster exceeds the materialized guest capacity")
    retainer_capacity = (
        RETAINER_CAPACITY_BASE + int(assets.building_levels[BuildingKeys.JIADING_FANG]) * RETAINER_CAPACITY_PER_LEVEL
    )
    if int(assets.retainer_count) > retainer_capacity:
        raise BootstrapMaterializationError("bootstrap retainers exceed the materialized retainer capacity")

    inventory_by_key = {entry.key: entry for entry in catalog.inventory}
    grain_inventory_quantity = 0
    for target in assets.inventory:
        entry = inventory_by_key.get(target.template_key)
        if entry is None or not entry.tradeable:
            raise BootstrapMaterializationError("bootstrap inventory references an unavailable tradeable template")
        if target.template_key == "grain" and target.storage_location == InventoryItem.StorageLocation.WAREHOUSE:
            grain_inventory_quantity += int(target.quantity)
    if grain_inventory_quantity > int(assets.grain):
        raise BootstrapMaterializationError("bootstrap grain inventory exceeds the target grain balance")

    manor.retainer_count = int(assets.retainer_count)
    manor.silver = int(assets.silver)
    manor.grain = int(assets.grain) - grain_inventory_quantity
    manor.resource_updated_at = now
    return grain_inventory_quantity


def _materialize_technologies(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    account_created_at: datetime,
) -> tuple[PlayerTechnology, ...]:
    catalog_by_key = {entry.key: entry for entry in catalog.technologies}
    if set(assets.technology_levels) != set(catalog_by_key):
        raise BootstrapMaterializationError("bootstrap technology targets do not match the locked catalog")
    reached_at_by_key: dict[str, datetime] = {}
    rows: list[PlayerTechnology] = []
    for key in sorted(assets.technology_levels):
        level = int(assets.technology_levels[key])
        if level < 0 or level > int(catalog_by_key[key].max_level):
            raise BootstrapMaterializationError(f"bootstrap technology level is outside the catalog limit: {key}")
        reached_at = _history_time(
            account_created_at,
            int(assets.technology_reached_day_offsets[key]),
        )
        reached_at_by_key[key] = reached_at
        rows.append(
            PlayerTechnology(
                manor=manor,
                tech_key=key,
                level=level,
                is_upgrading=False,
                upgrade_complete_at=None,
                created_at=reached_at,
                updated_at=reached_at,
            )
        )
    if rows:
        rows = list(PlayerTechnology.objects.bulk_create(rows))
        if any(row.pk is None for row in rows):
            rows = list(
                PlayerTechnology.objects.filter(
                    manor=manor,
                    tech_key__in=sorted(assets.technology_levels),
                ).order_by("tech_key")
            )
        if tuple(row.tech_key for row in rows) != tuple(sorted(assets.technology_levels)):
            raise BootstrapMaterializationError("materialized technology set does not match the blueprint")
        for row in rows:
            row.created_at = reached_at_by_key[row.tech_key]
            row.updated_at = reached_at_by_key[row.tech_key]
        PlayerTechnology.objects.bulk_update(rows, ["created_at", "updated_at"])
    return tuple(rows)


def _validate_guest_repeats(
    *,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
) -> None:
    catalog_by_key = {entry.key: entry for entry in catalog.guests}
    counts = Counter(target.template_key for target in assets.guests)
    for key, count in counts.items():
        entry = catalog_by_key.get(key)
        if entry is None or not entry.recruitable:
            raise BootstrapMaterializationError("bootstrap guest references an unavailable recruitable template")
        if count > 1 and (entry.is_hermit or RARITY_RANK.get(entry.rarity, 99) > RARITY_RANK["green"]):
            raise BootstrapMaterializationError(f"bootstrap guest template cannot be repeated: {key}")


def _materialize_guests(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    context: RandomContext,
    config: Mapping[str, Any],
    account_created_at: datetime,
    now: datetime,
    growth_stage: int,
) -> tuple[tuple[Guest, ...], tuple[GearItem, ...], tuple[GuestSkill, ...]]:
    _validate_guest_repeats(assets=assets, catalog=catalog)
    guest_catalog = {entry.key: entry for entry in catalog.guests}
    guest_templates = _load_exact_templates(
        GuestTemplate,
        {target.template_key for target in assets.guests},
        label="guest",
    )
    gear_catalog = {entry.key: entry for entry in catalog.gear}
    gear_keys = {key for target in assets.guests for key in target.gear_template_keys}
    gear_templates = _load_exact_templates(
        GearTemplate,
        gear_keys,
        label="gear",
    )
    skill_catalog = {entry.key: entry for entry in catalog.skills}
    skill_keys = {key for target in assets.guests for key in target.skill_keys}
    skill_templates = _load_exact_templates(
        Skill,
        skill_keys,
        label="skill",
    )
    guest_max_rarity = _configured_max_rarity(
        config,
        field="guest_max_rarity_by_stage",
        growth_stage=growth_stage,
    )
    gear_max_rarity = _configured_max_rarity(
        config,
        field="gear_max_rarity_by_stage",
        growth_stage=growth_stage,
    )

    guests: list[Guest] = []
    guest_by_ordinal: dict[int, Guest] = {}
    for target in assets.guests:
        catalog_entry = guest_catalog[target.template_key]
        if RARITY_RANK.get(catalog_entry.rarity, 99) > guest_max_rarity:
            raise BootstrapMaterializationError("bootstrap guest exceeds the stage rarity cap")
        rng = guest_random(
            context,
            ordinal=target.ordinal,
            template_key=target.template_key,
        )
        guest = create_guest_from_template(
            manor=manor,
            template=guest_templates[target.template_key],
            rng=rng,
            grant_skills=False,
            save=False,
        )
        expected = guest_seed_attributes(
            context,
            ordinal=target.ordinal,
            template=catalog_entry,
        )
        actual = (
            int(guest.force),
            int(guest.intellect),
            int(guest.defense_stat),
            int(guest.agility),
            int(guest.luck),
        )
        if actual != (
            expected.force,
            expected.intellect,
            expected.defense,
            expected.agility,
            expected.luck,
        ):
            raise BootstrapMaterializationError("guest projection changed after bootstrap planning")
        if int(target.level) > int(GUEST.MAX_LEVEL):
            raise BootstrapMaterializationError("bootstrap guest exceeds the guest level limit")
        guest.level = int(target.level)
        guest_by_ordinal[target.ordinal] = guest
        guests.append(guest)
    if guests:
        expected_template_ids = tuple(int(guest.template_id) for guest in guests)
        guests = list(Guest.objects.bulk_create(guests))
        if any(guest.pk is None for guest in guests):
            guests = list(Guest.objects.filter(manor=manor).select_related("template").order_by("id"))
        if tuple(int(guest.template_id) for guest in guests) != expected_template_ids:
            raise BootstrapMaterializationError("materialized guest order does not match the blueprint")
        guest_by_ordinal = {target.ordinal: guest for target, guest in zip(assets.guests, guests, strict=True)}

    gear_rows: list[GearItem] = []
    guest_update_fields = {
        "level",
        "current_hp",
        "attack_bonus",
        "defense_bonus",
    }
    for target in assets.guests:
        guest = guest_by_ordinal[target.ordinal]
        slot_counts: Counter[str] = Counter()
        for key in target.gear_template_keys:
            gear_entry = gear_catalog.get(key)
            if gear_entry is None:
                raise BootstrapMaterializationError("bootstrap gear references an unavailable template")
            if RARITY_RANK.get(gear_entry.rarity, 99) > gear_max_rarity:
                raise BootstrapMaterializationError("bootstrap gear exceeds the stage rarity cap")
            slot_counts[gear_entry.slot] += 1
            if slot_counts[gear_entry.slot] > slot_capacity(gear_entry.slot):
                raise BootstrapMaterializationError("bootstrap gear exceeds a guest slot capacity")
            template = gear_templates[key]
            apply_template_stats_to_guest(
                guest,
                template,
                +1,
                guest_update_fields,
            )
            gear_rows.append(
                GearItem(
                    manor=manor,
                    template=template,
                    guest=guest,
                    inventory_backed=False,
                    level=1,
                )
            )
        guest.current_hp = guest.max_hp
    if gear_rows:
        expected_gear_links = tuple(_gear_link_identity(row) for row in gear_rows)
        gear_rows = list(GearItem.objects.bulk_create(gear_rows))
        if any(row.pk is None for row in gear_rows):
            gear_rows = list(GearItem.objects.filter(manor=manor).select_related("template").order_by("id"))
        if tuple(_gear_link_identity(row) for row in gear_rows) != expected_gear_links:
            raise BootstrapMaterializationError("materialized gear order does not match the blueprint")
    gear_by_guest_id: dict[int, list[GearItem]] = {}
    for row in gear_rows:
        if row.guest_id is None:
            raise BootstrapMaterializationError("materialized gear is missing its guest identity")
        gear_by_guest_id.setdefault(int(row.guest_id), []).append(row)
    if guests:
        Guest.objects.bulk_update(guests, sorted(guest_update_fields))
    for guest in guests:
        apply_set_bonuses(
            guest,
            gear_items=gear_by_guest_id.get(int(guest.id), ()),
            persist=False,
        )
        guest.current_hp = guest.max_hp
    if guests:
        Guest.objects.bulk_update(
            guests,
            sorted({*SET_STAT_FIELD_MAP.values(), "gear_set_bonus"}),
        )

    skill_rows: list[GuestSkill] = []
    for target in assets.guests:
        guest = guest_by_ordinal[target.ordinal]
        if len(target.skill_keys) > int(GUEST.MAX_SKILL_SLOTS):
            raise BootstrapMaterializationError("bootstrap skills exceed the guest skill capacity")
        template_initial_skills = set(guest_catalog[target.template_key].initial_skill_keys)
        for key, offset in zip(
            target.skill_keys,
            target.skill_learned_day_offsets,
            strict=True,
        ):
            skill_entry = skill_catalog.get(key)
            if skill_entry is None:
                raise BootstrapMaterializationError("bootstrap skill references an unavailable template")
            if not (
                int(guest.level) >= skill_entry.required_level
                and int(guest.force) >= skill_entry.required_force
                and int(guest.intellect) >= skill_entry.required_intellect
                and int(guest.defense_stat) >= skill_entry.required_defense
                and int(guest.agility) >= skill_entry.required_agility
            ):
                raise BootstrapMaterializationError("bootstrap guest does not satisfy a skill requirement")
            skill_rows.append(
                GuestSkill(
                    guest=guest,
                    skill=skill_templates[key],
                    source=(GuestSkill.Source.TEMPLATE if key in template_initial_skills else GuestSkill.Source.BOOK),
                    learned_at=_history_time(account_created_at, int(offset)),
                )
            )
    if skill_rows:
        expected_skill_links = tuple((int(row.guest_id), int(row.skill_id)) for row in skill_rows)
        skill_rows = list(GuestSkill.objects.bulk_create(skill_rows))
        if any(row.pk is None for row in skill_rows):
            skill_rows = list(GuestSkill.objects.filter(guest__manor=manor).order_by("id"))
        if tuple((int(row.guest_id), int(row.skill_id)) for row in skill_rows) != expected_skill_links:
            raise BootstrapMaterializationError("materialized skill order does not match the blueprint")

    for target in assets.guests:
        guest = guest_by_ordinal[target.ordinal]
        guest.created_at = _history_time(
            account_created_at,
            int(target.created_day_offset),
        )
        guest.last_hp_recovery_at = now
    if guests:
        Guest.objects.bulk_update(
            guests,
            ["created_at", "last_hp_recovery_at", "current_hp"],
        )
    for target in assets.guests:
        guest = guest_by_ordinal[target.ordinal]
        target_gears = gear_by_guest_id.get(int(guest.id), [])
        if len(target_gears) != len(target.gear_acquired_day_offsets):
            raise BootstrapMaterializationError("materialized gear history does not match the blueprint")
        for gear, offset in zip(
            target_gears,
            target.gear_acquired_day_offsets,
            strict=True,
        ):
            gear.acquired_at = _history_time(account_created_at, int(offset))
    if gear_rows:
        GearItem.objects.bulk_update(gear_rows, ["acquired_at"])
    return tuple(guests), tuple(gear_rows), tuple(skill_rows)


def _materialize_troops(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    virtual_troop_capacity: int | None = None,
) -> tuple[PlayerTroop, ...]:
    troop_catalog = {entry.key: entry for entry in catalog.troops}
    target_keys = set(assets.troop_counts)
    excluded_keys = sorted(target_keys & VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS)
    if excluded_keys:
        raise BootstrapMaterializationError(
            "virtual-player bootstrap cannot materialize excluded troop templates: " + ", ".join(excluded_keys)
        )
    if not target_keys <= set(troop_catalog):
        raise BootstrapMaterializationError("bootstrap troops reference templates outside the locked catalog")
    troop_total = sum(int(count) for count in assets.troop_counts.values())
    if troop_total > MANOR_TROOP_CAPACITY:
        raise BootstrapMaterializationError(
            f"bootstrap troop total {troop_total} exceeds manor capacity {MANOR_TROOP_CAPACITY}"
        )
    if virtual_troop_capacity is not None and troop_total > int(virtual_troop_capacity):
        raise BootstrapMaterializationError(
            f"bootstrap troop total {troop_total} exceeds prestige-band capacity {int(virtual_troop_capacity)}"
        )
    troop_templates = _load_exact_templates(
        TroopTemplate,
        target_keys,
        label="troop",
    )
    for key in target_keys:
        troop_class = troop_catalog[key].troop_class
        if troop_class == "scout":
            continue
        recruit_key = f"{troop_class}_recruit"
        if int(assets.technology_levels.get(recruit_key, 0)) < 1:
            raise BootstrapMaterializationError(f"bootstrap troop lacks its recruitment technology: {key}")
    rows = [
        PlayerTroop(
            manor=manor,
            troop_template=troop_templates[key],
            count=int(assets.troop_counts[key]),
        )
        for key in sorted(target_keys)
    ]
    if rows:
        expected_template_ids = tuple(int(row.troop_template_id) for row in rows)
        rows = list(PlayerTroop.objects.bulk_create(rows))
        if any(row.pk is None for row in rows):
            rows = list(PlayerTroop.objects.filter(manor=manor).order_by("id"))
        if tuple(int(row.troop_template_id) for row in rows) != expected_template_ids:
            raise BootstrapMaterializationError("materialized troop order does not match the blueprint")
    return tuple(rows)


def _materialize_inventory(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    config: Mapping[str, Any],
    account_created_at: datetime,
    now: datetime,
) -> tuple[InventoryItem, ...]:
    catalog_by_key = {entry.key: entry for entry in catalog.inventory}
    item_templates = _load_exact_templates(
        ItemTemplate,
        {target.template_key for target in assets.inventory} | {GRAIN_ITEM_KEY},
        label="inventory",
        allow_missing=frozenset({GRAIN_ITEM_KEY}),
    )
    rows: list[InventoryItem] = []
    for target in sorted(
        assets.inventory,
        key=lambda item: (item.template_key, item.storage_location),
    ):
        catalog_entry = catalog_by_key.get(target.template_key)
        if catalog_entry is None or not catalog_entry.tradeable:
            raise BootstrapMaterializationError("bootstrap inventory template is not available or tradeable")
        if (
            target.storage_location == InventoryItem.StorageLocation.TREASURY
            and target.template_key in TREASURY_BLOCKED_ITEM_KEYS
        ):
            raise BootstrapMaterializationError("bootstrap inventory cannot place protected resources in the treasury")
        template = item_templates[target.template_key]
        allowed = apply_inventory_daily_caps(
            template,
            quantity=int(target.quantity),
            config=dict(config),
            now=now,
        )
        if allowed != int(target.quantity):
            raise BootstrapMaterializationError("inventory daily cap cannot reserve the full blueprint quantity")
        item = add_item_to_inventory_locked(
            manor,
            target.template_key,
            int(target.quantity),
            storage_location=target.storage_location,
        )
        item.created_at = _history_time(
            account_created_at,
            int(target.acquired_day_offset),
        )
        item.updated_at = item.created_at
        rows.append(item)
    if rows:
        InventoryItem.objects.bulk_update(rows, ["created_at", "updated_at"])
    set_warehouse_grain_quantity_locked(
        manor,
        int(assets.grain),
        grain_template=item_templates.get(GRAIN_ITEM_KEY),
        grain_template_resolved=True,
    )
    if int(manor.grain) != int(assets.grain):
        raise BootstrapMaterializationError("materialized grain balance does not match the blueprint")
    return tuple(rows)


def materialize_bootstrap_assets(
    *,
    manor: Manor,
    assets: BootstrapAssetTargets,
    catalog: BootstrapCatalog,
    context: RandomContext,
    config: Mapping[str, Any],
    account_created_at: datetime,
    now: datetime,
    growth_stage: int,
    virtual_troop_capacity: int | None = None,
) -> MaterializedBootstrapAssets:
    _require_atomic()
    # The inventory and grain ledger are written below; hold the Manor row for
    # the whole materialization so capacity and compatibility updates share the
    # same lock contract as runtime reward paths.
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    buildings = _materialize_buildings(
        manor=manor,
        assets=assets,
        catalog=catalog,
        account_created_at=account_created_at,
    )
    _validate_manor_capacity_and_resources(
        manor=manor,
        assets=assets,
        catalog=catalog,
        now=now,
    )
    manor.save(
        update_fields=[
            "silver_capacity",
            "grain_capacity",
            "retainer_count",
            "silver",
            "grain",
            "resource_updated_at",
        ]
    )
    technologies = _materialize_technologies(
        manor=manor,
        assets=assets,
        catalog=catalog,
        account_created_at=account_created_at,
    )
    guests, gear, skills = _materialize_guests(
        manor=manor,
        assets=assets,
        catalog=catalog,
        context=context,
        config=config,
        account_created_at=account_created_at,
        now=now,
        growth_stage=growth_stage,
    )
    troops = _materialize_troops(
        manor=manor,
        assets=assets,
        catalog=catalog,
        virtual_troop_capacity=virtual_troop_capacity,
    )
    inventory = _materialize_inventory(
        manor=manor,
        assets=assets,
        catalog=catalog,
        config=config,
        account_created_at=account_created_at,
        now=now,
    )
    return MaterializedBootstrapAssets(
        buildings=buildings,
        technologies=technologies,
        guests=guests,
        gear=gear,
        skills=skills,
        troops=troops,
        inventory=inventory,
    )


__all__ = [
    "BootstrapMaterializationError",
    "MaterializedBootstrapAssets",
    "materialize_bootstrap_assets",
]
