from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from gameplay.management.commands._virtual_player_gate_c import (
    checksum,
    invoke_application_service,
    non_empty_text,
    write_operation_summary,
)
from gameplay.services.virtual_player_core import safety_baselines
from gameplay.services.virtual_player_core.config import V2_PRESTIGE_BAND_NAMES


class Command(BaseCommand):
    help = "Validate and freeze one immutable pre-activation Arena shortage baseline; " "defaults to dry-run."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--mode",
            choices=safety_baselines.ARENA_SHORTAGE_BASELINE_MODES,
            required=True,
        )
        parser.add_argument(
            "--prestige-band",
            choices=V2_PRESTIGE_BAND_NAMES,
            required=True,
        )
        parser.add_argument("--baseline-ratio", required=True)
        parser.add_argument("--evidence-id", required=True)
        parser.add_argument("--evidence-checksum", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        apply = bool(options["apply"])
        summary = invoke_application_service(
            lambda: safety_baselines.freeze_arena_shortage_baseline_operation(
                mode=options["mode"],
                prestige_band=options["prestige_band"],
                baseline_ratio=options["baseline_ratio"],
                evidence_id=non_empty_text(options["evidence_id"], option_name="--evidence-id"),
                evidence_checksum=checksum(
                    options["evidence_checksum"],
                    option_name="--evidence-checksum",
                ),
                apply=apply,
            )
        )
        write_operation_summary(
            self,
            summary,
            apply=apply,
            details=(
                ("mode_scope", summary.mode),
                ("prestige_band", summary.prestige_band),
                ("baseline_ratio", summary.baseline_ratio),
                ("payload_digest", summary.payload_digest),
            ),
        )
