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
        parser.add_argument("--calibration-route", action="append", default=None)
        parser.add_argument(
            "--clear-calibration-routes",
            action="store_true",
            help="Explicitly clear every persisted calibration route.",
        )
        pause_reason = parser.add_mutually_exclusive_group()
        pause_reason.add_argument("--pause-reason", default=None)
        pause_reason.add_argument("--clear-pause-reason", action="store_true")
        parser.add_argument("--expected-pause-reason", default=None)
        parser.add_argument(
            "--resume-paused",
            action="store_true",
            help="Explicitly resume a V2_ACTIVE-origin safety pause.",
        )
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        expected_absent = bool(options["expected_absent"])
        raw_calibration_routes = options["calibration_route"]
        clear_calibration_routes = bool(options["clear_calibration_routes"])
        clear_pause_reason = bool(options["clear_pause_reason"])
        resume_paused = bool(options["resume_paused"])
        calibration_routes: tuple[dict[str, Any], ...] | None
        if clear_calibration_routes and raw_calibration_routes:
            raise CommandError("--clear-calibration-routes cannot be combined with --calibration-route")
        if expected_absent:
            expected_revision = None
            if options["expected_bootstrap_mode"] is not None or options["expected_maintenance_mode"] is not None:
                raise CommandError("expected current modes must be omitted with --expected-absent")
            if clear_calibration_routes:
                raise CommandError("--clear-calibration-routes requires --expected-revision")
            if raw_calibration_routes:
                raise CommandError("--calibration-route requires --expected-revision")
            if (
                options["pause_reason"] is not None
                or clear_pause_reason
                or options["expected_pause_reason"] is not None
                or resume_paused
            ):
                raise CommandError("pause controls require --expected-revision")
            calibration_routes = ()
        else:
            expected_revision = non_negative_int(
                options["expected_revision"],
                option_name="--expected-revision",
            )
            if options["expected_bootstrap_mode"] is None or options["expected_maintenance_mode"] is None:
                raise CommandError(
                    "--expected-bootstrap-mode and --expected-maintenance-mode are required with --expected-revision"
                )
            if clear_calibration_routes:
                calibration_routes = ()
            elif raw_calibration_routes is None:
                calibration_routes = None
            else:
                calibration_routes = json_mappings(raw_calibration_routes, option_name="--calibration-route")
        if resume_paused:
            if options["expected_maintenance_mode"] != MaintenanceMode.V2_PAUSED.value:
                raise CommandError("--resume-paused requires --expected-maintenance-mode=v2_paused")
            if options["maintenance_mode"] != MaintenanceMode.V2_ACTIVE.value:
                raise CommandError("--resume-paused requires --maintenance-mode=v2_active")
            if options["expected_pause_reason"] is None:
                raise CommandError("--resume-paused requires --expected-pause-reason")
            clear_pause_reason = True
        elif options["expected_pause_reason"] is not None:
            raise CommandError("--expected-pause-reason requires --resume-paused")
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: runtime_configs.transition_virtual_player_routing_operation(
                expected_revision=expected_revision,
                expected_bootstrap_mode=options["expected_bootstrap_mode"],
                expected_maintenance_mode=options["expected_maintenance_mode"],
                bootstrap_mode=options["bootstrap_mode"],
                maintenance_mode=options["maintenance_mode"],
                calibration_routes=calibration_routes,
                pause_reason=options["pause_reason"],
                clear_pause_reason=clear_pause_reason,
                expected_pause_reason=options["expected_pause_reason"],
                resume_paused=resume_paused,
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
                ("calibration_route_count", len(snapshot.calibration_routes)),
                ("pause_reason", snapshot.pause_reason),
                ("paused_from_maintenance_mode", snapshot.paused_from_maintenance_mode),
                ("persisted", snapshot.persisted),
            ),
        )
