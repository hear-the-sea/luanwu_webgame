from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gameplay.constants import REGION_CHOICES
from gameplay.models import BotProfile
from gameplay.services.virtual_players import create_virtual_players_for_band, virtual_player_prestige_bands


class Command(BaseCommand):
    help = "Generate virtual player manors for operations."

    def add_arguments(self, parser):
        region_choices = [key for key, _label in REGION_CHOICES if key != "overseas"]
        parser.add_argument("--region", required=True, choices=region_choices)
        parser.add_argument("--prestige-band", required=True)
        parser.add_argument("--count", type=int, default=1)
        parser.add_argument("--archetype", choices=[choice for choice, _label in BotProfile.Archetype.choices])
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        region = str(options["region"])
        prestige_band = str(options["prestige_band"])
        count = int(options["count"])
        archetype = options.get("archetype")
        dry_run = bool(options.get("dry_run"))

        if count <= 0:
            raise CommandError("--count must be positive")

        bands = virtual_player_prestige_bands()
        if prestige_band not in bands:
            raise CommandError(f"unknown prestige band: {prestige_band}")

        if dry_run:
            self.stdout.write(
                f"dry-run: would create {count} virtual players in region={region} prestige_band={prestige_band}"
            )
            return

        now = timezone.now()
        created = len(
            create_virtual_players_for_band(
                region=region,
                prestige_band=prestige_band,
                archetype=archetype,
                count=count,
                now=now,
            )
        )

        self.stdout.write(self.style.SUCCESS(f"created {created} virtual players"))
