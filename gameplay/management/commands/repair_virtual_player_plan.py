from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    non_empty_text,
    positive_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import profile_management


class Command(BaseCommand):
    help = "Replay and repair one V2 development plan at its expected schema; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--profile-id", type=int, required=True)
        parser.add_argument("--expected-plan-schema-version", type=int, required=True)
        parser.add_argument("--recovery-basis", "--reason", dest="recovery_basis", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        profile_id = positive_int(options["profile_id"], option_name="--profile-id")
        expected_plan_schema_version = positive_int(
            options["expected_plan_schema_version"],
            option_name="--expected-plan-schema-version",
        )
        recovery_basis = non_empty_text(options["recovery_basis"], option_name="--recovery-basis")
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: profile_management.repair_virtual_player_plan(
                profile_id=profile_id,
                expected_plan_schema_version=expected_plan_schema_version,
                recovery_basis=recovery_basis,
                apply=apply,
            )
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(("profile_id", profile_id), ("recovery_basis", recovery_basis)),
        )
