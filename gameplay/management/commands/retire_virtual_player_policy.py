from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    add_policy_version_argument,
    checksum,
    invoke_application_service,
    positive_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import policy_registry


class Command(BaseCommand):
    help = "Retire an unreferenced virtual-player policy after its replay guard; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        add_policy_version_argument(parser)
        parser.add_argument("--expected-checksum", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        version = positive_int(options["version"], option_name="--version")
        expected_checksum = checksum(options["expected_checksum"], option_name="--expected-checksum")
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: policy_registry.retire_policy_release_operation(
                version=version,
                expected_checksum=expected_checksum,
                apply=apply,
            )
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(("version", summary.version), ("checksum", summary.checksum)),
        )
