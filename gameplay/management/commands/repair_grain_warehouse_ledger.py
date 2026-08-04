from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.core import set_warehouse_grain_quantity_locked


class Command(BaseCommand):
    help = "以 Manor.grain 兼容字段为准，补建缺失或校准不一致的仓库粮食账本。"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500, help="每批处理的庄园数量。")

    def handle(self, *args, **options):
        batch_size = max(1, int(options.get("batch_size") or 500))
        processed = 0
        repaired = 0
        calibrated = 0
        preserved = 0
        grain_template = ItemTemplate.objects.filter(key="grain").first()
        if grain_template is None:
            self.stdout.write(self.style.WARNING("未找到粮食模板，未执行账本修复。"))
            return
        for manor_id in Manor.objects.order_by("id").values_list("id", flat=True).iterator(chunk_size=batch_size):
            with transaction.atomic():
                manor = Manor.objects.select_for_update().get(pk=manor_id)
                ledger_row = (
                    InventoryItem.objects.select_for_update()
                    .filter(
                        manor=manor,
                        template=grain_template,
                        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                    )
                    .first()
                )
                target_quantity = int(manor.grain or 0)
                if ledger_row is None:
                    set_warehouse_grain_quantity_locked(
                        manor,
                        target_quantity,
                        grain_template=grain_template,
                        grain_template_resolved=True,
                    )
                    repaired += 1
                elif int(ledger_row.quantity or 0) == target_quantity:
                    preserved += 1
                else:
                    set_warehouse_grain_quantity_locked(
                        manor,
                        target_quantity,
                        grain_template=grain_template,
                        grain_template_resolved=True,
                    )
                    calibrated += 1
            processed += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"已检查 {processed} 个庄园，补建 {repaired} 个账本，"
                f"校准 {calibrated} 个账本，保留 {preserved} 个一致账本。"
            )
        )
