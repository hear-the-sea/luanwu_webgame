from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gameplay.models import BotMaintenanceRecovery
from gameplay.services.virtual_player_core.recovery import requeue_recovery


class Command(BaseCommand):
    help = "List or formally requeue a durable virtual-player recovery record."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--list",
            action="store_true",
            help="List recovery records instead of changing one.",
        )
        parser.add_argument("--scope", help="Recovery scope, for example profile or arena_member.")
        parser.add_argument("--entity-key", dest="entity_key", help="Exact durable recovery entity key.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum rows to list.")
        parser.add_argument(
            "--reason",
            default="management_command",
            help="Audit reason written when a record is requeued.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        scope = str(options.get("scope") or "").strip()
        entity_key = str(options.get("entity_key") or "").strip()
        if bool(options["list"]):
            self._list_records(scope=scope, entity_key=entity_key, limit=int(options["limit"]))
            return
        if not scope or not entity_key:
            raise CommandError("--scope and --entity-key are required unless --list is used")
        try:
            row = requeue_recovery(
                scope=scope,
                entity_key=entity_key,
                reason=str(options["reason"] or "management_command"),
            )
        except BotMaintenanceRecovery.DoesNotExist as exc:
            raise CommandError(f"recovery record not found: {scope}:{entity_key}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"requeued scope={row.scope} entity_key={row.entity_key} "
                f"next_retry_at={row.next_retry_at.isoformat() if row.next_retry_at else ''}"
            )
        )

    def _list_records(self, *, scope: str, entity_key: str, limit: int) -> None:
        normalized_limit = max(0, min(1_000, int(limit)))
        queryset = BotMaintenanceRecovery.objects.order_by("status", "next_retry_at", "id")
        if scope:
            queryset = queryset.filter(scope=scope)
        if entity_key:
            queryset = queryset.filter(entity_key=entity_key)
        rows = queryset[:normalized_limit]
        printed = 0
        for row in rows:
            printed += 1
            self.stdout.write(
                "scope=%s entity_key=%s status=%s failure_code=%s streak=%s next_retry_at=%s"
                % (
                    row.scope,
                    row.entity_key,
                    row.status,
                    row.failure_code,
                    row.failure_streak,
                    row.next_retry_at.isoformat() if row.next_retry_at else "",
                )
            )
        if printed == 0:
            self.stdout.write("recovery=none")
