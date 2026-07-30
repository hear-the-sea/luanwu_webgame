from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from gameplay.models import ItemTemplate
from gameplay.services.equipment_template_sync import LEGACY_EQUIPMENT_KEY_ALIASES, synchronize_equipment_templates
from guests.utils.equipment_utils import EQUIP_SLOT_MAP


class Command(BaseCommand):
    help = "Synchronize materialized GearTemplate rows and merge known legacy equipment keys."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the full repair in a transaction and roll it back.",
        )
        parser.add_argument(
            "--sets-only",
            action="store_true",
            help="Limit synchronization to equipment set members and known legacy aliases.",
        )

    def handle(self, *args: object, **options: object) -> None:
        equipment_items = list(
            ItemTemplate.objects.filter(effect_type__in=tuple(EQUIP_SLOT_MAP))
            .exclude(key__in=tuple(LEGACY_EQUIPMENT_KEY_ALIASES))
            .order_by("key")
            .only("key", "effect_payload")
        )
        if options.get("sets_only"):
            alias_targets = set(LEGACY_EQUIPMENT_KEY_ALIASES.values())
            item_keys = [
                item.key
                for item in equipment_items
                if item.key in alias_targets
                or (isinstance(item.effect_payload, dict) and item.effect_payload.get("set_key"))
            ]
        else:
            item_keys = [item.key for item in equipment_items]
        dry_run = bool(options.get("dry_run"))
        report = synchronize_equipment_templates(item_keys, dry_run=dry_run)
        prefix = "DRY RUN" if dry_run else "APPLIED"
        scope = "sets" if options.get("sets_only") else "all equipment"
        self.stdout.write(
            self.style.SUCCESS(
                f"[{prefix}] equipment template sync ({scope}): "
                f"created={report.gear_templates_created}, "
                f"updated={report.gear_templates_updated}, "
                f"gear_reassigned={report.gear_items_reassigned}, "
                f"guests_reconciled={report.guests_reconciled}, "
                f"aliases_merged={report.item_aliases_merged}, "
                f"inventory_rekeyed={report.inventory_rows_rekeyed}, "
                f"related_rekeyed={report.related_rows_rekeyed}"
            )
        )
