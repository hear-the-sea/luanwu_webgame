from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    non_empty_text,
    positive_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import external_reconciliation


class Command(BaseCommand):
    help = "Requeue one quarantined virtual-player reconciliation; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--reconciliation-id", type=int, required=True)
        parser.add_argument("--expected-failure-code", required=True)
        parser.add_argument("--expected-attempt-count", type=int, required=True)
        parser.add_argument("--recovery-basis", "--reason", dest="recovery_basis", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        reconciliation_id = positive_int(
            options["reconciliation_id"],
            option_name="--reconciliation-id",
        )
        expected_failure_code = non_empty_text(
            options["expected_failure_code"],
            option_name="--expected-failure-code",
        )
        expected_attempt_count = positive_int(
            options["expected_attempt_count"],
            option_name="--expected-attempt-count",
            maximum=12,
        )
        recovery_basis = non_empty_text(
            options["recovery_basis"],
            option_name="--recovery-basis",
        )
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: external_reconciliation.requeue_quarantined_reconciliation_operation(
                reconciliation_id=reconciliation_id,
                expected_failure_code=expected_failure_code,
                expected_attempt_count=expected_attempt_count,
                recovery_basis=recovery_basis,
                apply=apply,
            )
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(
                ("reconciliation_id", reconciliation_id),
                ("expected_failure_code", expected_failure_code),
                ("expected_attempt_count", expected_attempt_count),
                ("recovery_basis", recovery_basis),
            ),
        )
