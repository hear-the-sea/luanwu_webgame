from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gameplay.services.virtual_player_core.runtime_preflight import (
    RuntimePreflightReport,
    initialize_virtual_player_v2_runtime,
    inspect_virtual_player_v2_runtime,
)


class Command(BaseCommand):
    help = "Audit or idempotently initialize the single policy-2 virtual-player runtime."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Publish policy 2 and converge routing; migrations and legacy-row cleanup are never implicit.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        try:
            report = initialize_virtual_player_v2_runtime(apply=apply) if apply else inspect_virtual_player_v2_runtime()
        except (ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self._write_report(report, apply=apply)
        if not report.ok:
            raise CommandError("virtual-player V2 preflight has blocking checks")

    def _write_report(self, report: RuntimePreflightReport, *, apply: bool) -> None:
        self.stdout.write(f"mode={'apply' if apply else 'dry-run'} status={'ok' if report.ok else 'blocked'}")
        for check in report.checks:
            status = "ok" if check.passed else check.severity
            self.stdout.write(f"check={check.code} status={status} detail={check.detail}")
