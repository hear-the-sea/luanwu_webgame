from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired: virtual players are materialized only by the policy-2 population consumer."

    def add_arguments(self, parser):
        parser.add_argument("--region", required=True)
        parser.add_argument("--prestige-band", required=True)
        parser.add_argument("--count", type=int, default=1)
        parser.add_argument("--archetype")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        raise CommandError(
            "direct virtual-player generation is retired; use the policy-2 population reconciliation consumer"
        )
