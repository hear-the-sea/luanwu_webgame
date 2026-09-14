import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gameplay.services.arena.virtual_fill_service import summarize_fill_service
from gameplay.services.virtual_player_core.config import V2_PRESTIGE_BAND_NAMES


class Command(BaseCommand):
    help = "只读输出竞技场补位结果、样本量及积压；不会恢复暂停或创建基线。"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--mode", choices=("tournament", "coop"))
        parser.add_argument("--prestige-band", choices=V2_PRESTIGE_BAND_NAMES)
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        if not 1 <= options["days"] <= 30 or not 1 <= options["limit"] <= 200:
            raise CommandError("days must be 1..30 and limit must be 1..200")
        now = timezone.now()
        report = summarize_fill_service(
            since=now - timedelta(days=options["days"]),
            mode=options["mode"],
            prestige_band=options["prestige_band"],
            limit=options["limit"],
            now=now,
        )
        self.stdout.write(json.dumps(report, ensure_ascii=False))
