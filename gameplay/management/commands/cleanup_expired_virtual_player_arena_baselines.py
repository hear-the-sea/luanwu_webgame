from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.services.virtual_player_core import safety_baselines


class Command(BaseCommand):
    help = "Dry-run or delete expired runtime Arena shortage baselines."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=1000)

    def handle(self, *args: Any, **options: Any) -> str:
        summary = safety_baselines.cleanup_expired_arena_shortage_baselines(
            limit=int(options["limit"]),
            apply=bool(options["apply"]),
        )
        mode = "apply" if options["apply"] else "dry-run"
        self.stdout.write(
            f"mode={mode} scanned={summary.scanned} expired={summary.expired} " f"deleted={summary.deleted}"
        )
        return mode
