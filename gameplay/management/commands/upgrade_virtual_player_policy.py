from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    checksum,
    invoke_application_service,
    non_negative_int,
    positive_int,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import profile_management


class Command(BaseCommand):
    help = "Move one deterministic V2 batch between immutable policy releases; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expected-policy-version", type=int, required=True)
        parser.add_argument("--expected-policy-checksum", required=True)
        parser.add_argument("--target-policy-version", type=int, required=True)
        parser.add_argument("--target-policy-checksum", required=True)
        parser.add_argument("--after-id", type=int, default=0)
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        expected_policy_version = positive_int(
            options["expected_policy_version"],
            option_name="--expected-policy-version",
        )
        target_policy_version = positive_int(
            options["target_policy_version"],
            option_name="--target-policy-version",
        )
        expected_policy_checksum = checksum(
            options["expected_policy_checksum"],
            option_name="--expected-policy-checksum",
        )
        target_policy_checksum = checksum(
            options["target_policy_checksum"],
            option_name="--target-policy-checksum",
        )
        after_id = non_negative_int(options["after_id"], option_name="--after-id")
        batch_size = positive_int(options["batch_size"], option_name="--batch-size", maximum=1000)
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: profile_management.upgrade_virtual_player_policy_batch(
                expected_policy_version=expected_policy_version,
                expected_policy_checksum=expected_policy_checksum,
                target_policy_version=target_policy_version,
                target_policy_checksum=target_policy_checksum,
                after_id=after_id,
                batch_size=batch_size,
                apply=apply,
            )
        )
        write_operation_summary(self, summary, apply=apply)
