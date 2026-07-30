from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    non_negative_int,
    positive_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import profile_management


class Command(BaseCommand):
    help = "Enroll one deterministic batch of eligible V1 profiles into V2; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        after_id = non_negative_int(options["after_id"], option_name="--after-id")
        batch_size = positive_int(options["batch_size"], option_name="--batch-size", maximum=1000)
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: profile_management.enroll_virtual_players_batch(
                after_id=after_id,
                batch_size=batch_size,
                apply=apply,
            )
        )
        write_operation_summary(self, summary, apply=apply)
