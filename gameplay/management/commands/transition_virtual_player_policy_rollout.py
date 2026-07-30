from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    invoke_application_service,
    non_negative_int,
    positive_int,
    write_operation_summary,
)
from gameplay.services import runtime_configs


class Command(BaseCommand):
    help = "CAS-transition persisted virtual-player policy rollout; defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expected-revision", type=int, required=True)
        parser.add_argument("--expected-target-version", type=int, required=True)
        expected = parser.add_mutually_exclusive_group(required=True)
        expected.add_argument("--expected-enabled", action="store_true")
        expected.add_argument("--expected-disabled", action="store_true")
        parser.add_argument("--expected-rollout-percent", type=int, required=True)
        parser.add_argument("--target-version", type=int, required=True)
        proposed = parser.add_mutually_exclusive_group(required=True)
        proposed.add_argument("--enable", action="store_true")
        proposed.add_argument("--disable", action="store_true")
        parser.add_argument("--rollout-percent", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        expected_revision = non_negative_int(
            options["expected_revision"],
            option_name="--expected-revision",
        )
        expected_target_version = positive_int(
            options["expected_target_version"],
            option_name="--expected-target-version",
        )
        expected_rollout_percent = non_negative_int(
            options["expected_rollout_percent"],
            option_name="--expected-rollout-percent",
        )
        target_version = positive_int(
            options["target_version"],
            option_name="--target-version",
        )
        rollout_percent = non_negative_int(
            options["rollout_percent"],
            option_name="--rollout-percent",
        )
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: runtime_configs.transition_virtual_player_policy_rollout_operation(
                expected_revision=expected_revision,
                expected_target_version=expected_target_version,
                expected_enabled=bool(options["expected_enabled"]),
                expected_rollout_percent=expected_rollout_percent,
                target_version=target_version,
                enabled=bool(options["enable"]),
                rollout_percent=rollout_percent,
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
                ("target_version", snapshot.target_version),
                ("enabled", snapshot.enabled),
                ("rollout_percent", snapshot.rollout_percent),
            ),
        )
