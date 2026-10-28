from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from gameplay.services.season.expeditions import advance_due_expeditions
from gameplay.services.season.lifecycle import advance_seasons


class Command(BaseCommand):
    help = "幂等推进出征、阵营生产与积分，归档旧赛季并衔接新的90天赛季。"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--watch", action="store_true", help="持续扫描，仅用于未启动Celery的本地开发")
        parser.add_argument("--interval", type=int, default=5, help="本地持续扫描间隔秒数，默认5秒")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["interval"] < 1:
            raise CommandError("扫描间隔至少1秒")
        while True:
            close_old_connections()
            self._settle()
            if not options["watch"]:
                return
            try:
                time.sleep(options["interval"])
            except KeyboardInterrupt:
                return

    def _settle(self) -> None:
        advance_due_expeditions()
        result = advance_seasons()
        self.stdout.write(
            self.style.SUCCESS(
                f"扫描 {result['seasons']} 个赛季，结算 {result['weeks']} 个周度战果，归档 {result['closed']} 个赛季，开启 {result['started']} 个新赛季。"
            )
        )
