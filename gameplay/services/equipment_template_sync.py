from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from gameplay.models import InventoryItem, ItemTemplate, Manor
from guests.models import GearItem, GearTemplate, Guest
from guests.services.equipment_payloads import build_gear_template_defaults, normalize_active_set_bonus, require_string
from guests.services.equipment_stats import apply_template_stats_to_guest
from guests.utils.equipment_utils import EQUIP_SLOT_MAP, SET_STAT_FIELD_MAP, compute_set_bonus

LEGACY_EQUIPMENT_KEY_ALIASES = {
    "equip_xiaoweitoukie": "equip_xiaoweitoukui",
}

_GEAR_TEMPLATE_SYNC_FIELDS = (
    "name",
    "slot",
    "rarity",
    "set_key",
    "set_description",
    "set_bonus",
    "attack_bonus",
    "defense_bonus",
    "extra_stats",
)


@dataclass(frozen=True)
class EquipmentTemplateSyncReport:
    gear_templates_created: int = 0
    gear_templates_updated: int = 0
    gear_items_reassigned: int = 0
    guests_reconciled: int = 0
    item_aliases_merged: int = 0
    inventory_rows_rekeyed: int = 0
    related_rows_rekeyed: int = 0

    @property
    def changed(self) -> bool:
        return any(
            (
                self.gear_templates_created,
                self.gear_templates_updated,
                self.gear_items_reassigned,
                self.guests_reconciled,
                self.item_aliases_merged,
                self.inventory_rows_rekeyed,
                self.related_rows_rekeyed,
            )
        )


def _apply_set_bonus_delta(guest: Guest, raw_bonus: object, *, sign: int, updates: set[str]) -> None:
    bonus = normalize_active_set_bonus(raw_bonus)
    for stat, value in bonus.items():
        field = SET_STAT_FIELD_MAP[stat]
        if value:
            setattr(guest, field, getattr(guest, field) + sign * value)
            updates.add(field)


def _merge_inventory_rows(old_item: ItemTemplate, target_item: ItemTemplate) -> int:
    moved = 0
    old_rows = list(old_item.inventory_entries.select_for_update().order_by("manor_id", "storage_location", "id"))
    for old_row in old_rows:
        target_row = (
            InventoryItem.objects.select_for_update()
            .filter(
                manor_id=old_row.manor_id,
                template=target_item,
                storage_location=old_row.storage_location,
            )
            .first()
        )
        if target_row is None:
            old_row.template = target_item
            old_row.save(update_fields=["template", "updated_at"])
        else:
            target_row.quantity += old_row.quantity
            target_row.save(update_fields=["quantity", "updated_at"])
            old_row.delete()
        moved += 1
    return moved


def _merge_other_item_references(old_item: ItemTemplate, target_item: ItemTemplate) -> int:
    from trade.models import AuctionDelivery, AuctionSlot, MarketListing

    moved = 0
    for model in (MarketListing, AuctionSlot, AuctionDelivery):
        moved += model.objects.filter(item_template=old_item).update(item_template=target_item)
    return moved


def _canonical_equipment_items(item_keys: Iterable[str]) -> dict[str, ItemTemplate]:
    requested_keys = {str(key) for key in item_keys}
    requested_keys.update(LEGACY_EQUIPMENT_KEY_ALIASES.values())
    requested_keys.difference_update(LEGACY_EQUIPMENT_KEY_ALIASES)
    if not requested_keys:
        return {}

    items = ItemTemplate.objects.select_for_update().filter(key__in=requested_keys)
    return {
        item.key: item
        for item in items
        if item.effect_type in EQUIP_SLOT_MAP and item.key not in LEGACY_EQUIPMENT_KEY_ALIASES
    }


def _canonical_gear_defaults(items: dict[str, ItemTemplate]) -> dict[str, dict[str, object]]:
    defaults: dict[str, dict[str, object]] = {}
    for key, item in items.items():
        slot = EQUIP_SLOT_MAP[item.effect_type]
        defaults[key] = build_gear_template_defaults(item, slot=slot)
    return defaults


def _lock_all_manors() -> None:
    # This administrative sync must not race normal Manor -> Guest equipment commands.
    list(Manor.objects.select_for_update().order_by("id").values_list("id", flat=True))


def _lock_legacy_trade_references() -> None:
    """Follow the runtime TradeRow -> Manor lock order before the global Manor lock."""
    from trade.models import AuctionDelivery, AuctionSlot, MarketListing

    legacy_item_ids = list(
        ItemTemplate.objects.filter(key__in=LEGACY_EQUIPMENT_KEY_ALIASES).order_by("id").values_list("id", flat=True)
    )
    if not legacy_item_ids:
        return

    # Discover through the item-template indexes without locking, then acquire
    # primary-key locks. Runtime status updates already hold the primary row and
    # must not deadlock against a secondary-index-first SELECT ... FOR UPDATE.
    listing_ids = list(
        MarketListing.objects.filter(item_template_id__in=legacy_item_ids).order_by("id").values_list("id", flat=True)
    )
    slot_ids = list(
        AuctionSlot.objects.filter(item_template_id__in=legacy_item_ids).order_by("id").values_list("id", flat=True)
    )
    delivery_ids = list(
        AuctionDelivery.objects.filter(item_template_id__in=legacy_item_ids).order_by("id").values_list("id", flat=True)
    )

    # Market commands lock a listing before its Manor. Auction settlement and
    # delivery similarly start from AuctionSlot/AuctionDelivery rows.
    list(
        MarketListing.objects.select_for_update().filter(id__in=listing_ids).order_by("id").values_list("id", flat=True)
    )
    list(AuctionSlot.objects.select_for_update().filter(id__in=slot_ids).order_by("id").values_list("id", flat=True))
    list(
        AuctionDelivery.objects.select_for_update()
        .filter(id__in=delivery_ids)
        .order_by("id")
        .values_list("id", flat=True)
    )


def _sync_locked_equipment_templates(
    canonical_items: dict[str, ItemTemplate],
    defaults_by_key: dict[str, dict[str, object]],
) -> tuple[int, int, int, int]:
    candidate_keys = set(canonical_items) | set(LEGACY_EQUIPMENT_KEY_ALIASES)
    templates_by_key = {
        template.key: template
        for template in GearTemplate.objects.select_for_update().filter(key__in=candidate_keys).order_by("id")
    }

    created_count = 0
    alias_template_targets: dict[int, GearTemplate] = {}
    for old_key, target_key in LEGACY_EQUIPMENT_KEY_ALIASES.items():
        old_template = templates_by_key.get(old_key)
        target_item = canonical_items.get(target_key)
        if old_template is None or target_item is None:
            continue
        target_template = templates_by_key.get(target_key)
        if target_template is None:
            target_template = GearTemplate.objects.create(key=target_key, **defaults_by_key[target_key])
            templates_by_key[target_key] = target_template
            created_count += 1
        alias_template_targets[old_template.id] = target_template

    for key, defaults in defaults_by_key.items():
        if key in templates_by_key:
            continue
        templates_by_key[key] = GearTemplate.objects.create(key=key, **defaults)
        created_count += 1

    changed_templates: dict[int, tuple[GearTemplate, dict[str, object]]] = {}
    for key, defaults in defaults_by_key.items():
        template = templates_by_key.get(key)
        if template is None:
            continue
        if any(getattr(template, field) != defaults[field] for field in _GEAR_TEMPLATE_SYNC_FIELDS):
            changed_templates[template.id] = (template, defaults)

    affected_template_ids = set(changed_templates) | set(alias_template_targets)
    if not affected_template_ids:
        return created_count, 0, 0, 0

    gear_items = list(
        GearItem.objects.select_for_update()
        .select_related("template")
        .filter(template_id__in=affected_template_ids)
        .order_by("id")
    )
    affected_guest_ids = sorted({gear.guest_id for gear in gear_items if gear.guest_id is not None})
    guests = {
        guest.id: guest
        for guest in Guest.objects.select_for_update().select_related("template").filter(id__in=affected_guest_ids)
    }

    for old_template_id, target_template in alias_template_targets.items():
        old_guest_ids = {gear.guest_id for gear in gear_items if gear.template_id == old_template_id and gear.guest_id}
        if (
            old_guest_ids
            and GearItem.objects.filter(
                guest_id__in=old_guest_ids,
                template=target_template,
            ).exists()
        ):
            raise AssertionError(f"cannot merge duplicate equipped templates into {target_template.key!r}")

    guest_updates: dict[int, set[str]] = defaultdict(set)
    affected_gears_by_guest: dict[int, list[GearItem]] = defaultdict(list)
    for gear in gear_items:
        if gear.guest_id is not None:
            affected_gears_by_guest[gear.guest_id].append(gear)

    for guest_id, guest_gears in affected_gears_by_guest.items():
        guest = guests[guest_id]
        updates = guest_updates[guest_id]
        _apply_set_bonus_delta(guest, guest.gear_set_bonus, sign=-1, updates=updates)
        for gear in guest_gears:
            apply_template_stats_to_guest(guest, gear.template, -1, updates)

    reassigned_count = 0
    for old_template_id, target_template in alias_template_targets.items():
        reassigned_count += GearItem.objects.filter(template_id=old_template_id).update(template=target_template)
        for gear in gear_items:
            if gear.template_id == old_template_id:
                gear.template_id = target_template.id
                gear.template = target_template

    for template, defaults in changed_templates.values():
        for field in _GEAR_TEMPLATE_SYNC_FIELDS:
            setattr(template, field, defaults[field])
        template.save(update_fields=list(_GEAR_TEMPLATE_SYNC_FIELDS))

    updated_templates_by_id = {
        template.id: template
        for template in GearTemplate.objects.filter(id__in={gear.template_id for gear in gear_items})
    }
    for guest_id, guest_gears in affected_gears_by_guest.items():
        guest = guests[guest_id]
        updates = guest_updates[guest_id]
        for gear in guest_gears:
            apply_template_stats_to_guest(guest, updated_templates_by_id[gear.template_id], +1, updates)

        current_set_bonus = normalize_active_set_bonus(compute_set_bonus(guest.gear_items.select_related("template")))
        _apply_set_bonus_delta(guest, current_set_bonus, sign=+1, updates=updates)
        guest.gear_set_bonus = current_set_bonus
        updates.add("gear_set_bonus")
        if guest.current_hp > guest.max_hp:
            guest.current_hp = guest.max_hp
            updates.add("current_hp")
        guest.save(update_fields=sorted(updates))

    for old_template_id in alias_template_targets:
        GearTemplate.objects.filter(pk=old_template_id).delete()

    return created_count, len(changed_templates), reassigned_count, len(affected_guest_ids)


def synchronize_equipment_templates(
    item_keys: Iterable[str],
    *,
    dry_run: bool = False,
) -> EquipmentTemplateSyncReport:
    normalized_keys = {require_string(key, field_name="equipment template sync item key") for key in item_keys}
    with transaction.atomic():
        _lock_legacy_trade_references()
        _lock_all_manors()
        canonical_items = _canonical_equipment_items(normalized_keys)
        defaults_by_key = _canonical_gear_defaults(canonical_items)
        created, updated, reassigned, reconciled = _sync_locked_equipment_templates(
            canonical_items,
            defaults_by_key,
        )

        alias_count = 0
        inventory_count = 0
        related_count = 0
        alias_keys = set(LEGACY_EQUIPMENT_KEY_ALIASES)
        old_items = {item.key: item for item in ItemTemplate.objects.select_for_update().filter(key__in=alias_keys)}
        for old_key, target_key in LEGACY_EQUIPMENT_KEY_ALIASES.items():
            old_item = old_items.get(old_key)
            target_item = canonical_items.get(target_key)
            if old_item is None or target_item is None:
                continue
            inventory_count += _merge_inventory_rows(old_item, target_item)
            related_count += _merge_other_item_references(old_item, target_item)
            old_item.delete()
            alias_count += 1

        report = EquipmentTemplateSyncReport(
            gear_templates_created=created,
            gear_templates_updated=updated,
            gear_items_reassigned=reassigned,
            guests_reconciled=reconciled,
            item_aliases_merged=alias_count,
            inventory_rows_rekeyed=inventory_count,
            related_rows_rekeyed=related_count,
        )
        if dry_run:
            transaction.set_rollback(True)
        return report
