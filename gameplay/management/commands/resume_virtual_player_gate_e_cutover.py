from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    non_negative_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import gate_e_cutover_workflow


class Command(BaseCommand):
    help = "Resume a safety-paused V2_CUTOVER after verified Gate E readiness; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expected-revision", type=int, required=True)
        parser.add_argument("--authorization-basis", default="")
        parser.add_argument("--expected-git-commit", default=None)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        operation_kwargs: dict[str, Any] = {
            "expected_revision": non_negative_int(options["expected_revision"], option_name="--expected-revision"),
            "authorization_basis": str(options["authorization_basis"]),
            "apply": apply,
        }
        if options["expected_git_commit"] is not None:
            operation_kwargs["expected_git_commit"] = options["expected_git_commit"]
        summary = invoke_application_service(
            lambda: gate_e_cutover_workflow.resume_gate_e_cutover_operation(**operation_kwargs)
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(
                ("revision", summary.snapshot.revision),
                ("maintenance_mode", summary.snapshot.maintenance_mode.value),
                (
                    "runtime_eligible_v1_profiles",
                    summary.runtime_eligible_v1_profiles,
                ),
                ("evidence_id", summary.evidence_id),
                ("evidence_digest", summary.evidence_digest),
                ("authorization_basis_digest", summary.authorization_basis_digest),
            ),
        )
