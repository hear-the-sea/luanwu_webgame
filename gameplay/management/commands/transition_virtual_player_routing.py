from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    json_mappings,
    non_negative_int,
    write_operation_summary,
)
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core.config import BootstrapMode, MaintenanceMode


class Command(BaseCommand):
    help = "Initialize or CAS-transition persisted virtual-player routing; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        expected = parser.add_mutually_exclusive_group(required=True)
        expected.add_argument("--expected-revision", type=int)
        expected.add_argument("--expected-absent", action="store_true")
        parser.add_argument("--expected-bootstrap-mode", choices=[mode.value for mode in BootstrapMode])
        parser.add_argument("--expected-maintenance-mode", choices=[mode.value for mode in MaintenanceMode])
        parser.add_argument("--bootstrap-mode", choices=[mode.value for mode in BootstrapMode], required=True)
        parser.add_argument("--maintenance-mode", choices=[mode.value for mode in MaintenanceMode], required=True)
        parser.add_argument("--calibration-route", action="append", default=[])
        parser.add_argument("--pause-reason", default="")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        expected_absent = bool(options["expected_absent"])
        if expected_absent:
            expected_revision = None
            if options["expected_bootstrap_mode"] is not None or options["expected_maintenance_mode"] is not None:
                raise CommandError("expected current modes must be omitted with --expected-absent")
        else:
            expected_revision = non_negative_int(
                options["expected_revision"],
                option_name="--expected-revision",
            )
            if options["expected_bootstrap_mode"] is None or options["expected_maintenance_mode"] is None:
                raise CommandError(
                    "--expected-bootstrap-mode and --expected-maintenance-mode are required with --expected-revision"
                )
        calibration_routes = json_mappings(options["calibration_route"], option_name="--calibration-route")
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: runtime_configs.transition_virtual_player_routing_operation(
                expected_revision=expected_revision,
                expected_bootstrap_mode=options["expected_bootstrap_mode"],
                expected_maintenance_mode=options["expected_maintenance_mode"],
                bootstrap_mode=options["bootstrap_mode"],
                maintenance_mode=options["maintenance_mode"],
                calibration_routes=calibration_routes,
                pause_reason=str(options["pause_reason"]),
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
                ("persisted", snapshot.persisted),
            ),
        )
