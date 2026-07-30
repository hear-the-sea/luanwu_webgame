from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    add_policy_version_argument,
    invoke_application_service,
    positive_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import policy_registry


class Command(BaseCommand):
    help = "Validate and release an immutable configured virtual-player policy; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        add_policy_version_argument(parser)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        version = positive_int(options["version"], option_name="--version")
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: policy_registry.release_configured_policy_operation(version=version, apply=apply)
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(("version", summary.version), ("checksum", summary.checksum)),
        )
