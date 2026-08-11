from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    checksum,
    invoke_application_service,
    non_empty_text,
    non_negative_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import policy_alignment


class Command(BaseCommand):
    help = "CAS-align the configured V2 policy release, profiles, and growth-control snapshot; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expected-routing-revision", type=int, required=True)
        parser.add_argument("--expected-pause-reason", required=True)
        parser.add_argument("--expected-policy-checksum", required=True)
        parser.add_argument("--target-policy-checksum", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: policy_alignment.align_configured_policy_runtime_operation(
                expected_routing_revision=non_negative_int(
                    options["expected_routing_revision"],
                    option_name="--expected-routing-revision",
                ),
                expected_pause_reason=non_empty_text(
                    options["expected_pause_reason"],
                    option_name="--expected-pause-reason",
                ),
                expected_policy_checksum=checksum(
                    options["expected_policy_checksum"],
                    option_name="--expected-policy-checksum",
                ),
                target_policy_checksum=checksum(
                    options["target_policy_checksum"],
                    option_name="--target-policy-checksum",
                ),
                apply=apply,
            )
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(
                ("policy_version", summary.version),
                ("previous_checksum", summary.previous_checksum),
                ("target_checksum", summary.target_checksum),
                ("profile_count", summary.profile_count),
                ("historical_v2_profile_count", summary.historical_v2_profile_count),
                ("updated_profile_count", summary.updated_profile_count),
                ("control_run_digest", summary.control_run_digest or "none"),
                ("routing_revision", summary.routing_revision),
                ("alignment_id", summary.alignment_id),
            ),
        )
