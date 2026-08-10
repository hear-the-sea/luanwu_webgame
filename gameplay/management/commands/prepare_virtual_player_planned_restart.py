from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    non_negative_int,
    write_operation_summary,
)
from gameplay.services import runtime_configs


class Command(BaseCommand):
    help = "Fence V2 virtual-player writes before a planned application restart; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expected-revision", type=int, required=True)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist the planned-restart fence; without this flag the command is a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        expected_revision = non_negative_int(
            options["expected_revision"],
            option_name="--expected-revision",
        )
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: runtime_configs.prepare_virtual_player_planned_restart_operation(
                expected_revision=expected_revision,
                apply=apply,
            )
        )
        snapshot = summary.snapshot
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(
                ("revision", snapshot.revision),
                ("bootstrap_mode", snapshot.bootstrap_mode.value),
                ("maintenance_mode", snapshot.maintenance_mode.value),
                ("pause_reason", snapshot.pause_reason),
                ("paused_from_maintenance_mode", snapshot.paused_from_maintenance_mode),
                ("persisted", snapshot.persisted),
            ),
        )
