"""Refresh current suite collection facts without altering historical Gate results."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import record_virtual_player_evidence as recorder  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/virtual_player_current_suite_collection.yaml")
    args = parser.parse_args()
    if args.output.name.startswith(("virtual_player_gate_", "virtual_player_gate_d1_")):
        parser.error("Collection-only output must not overwrite dated Gate evidence")
    selections = {
        "d1_contract": (recorder._read_makefile_paths("VIRTUAL_PLAYER_GATE_D1_CONTRACT_TESTS"), False),
        "d1_core_real_service": (recorder._read_makefile_paths("VIRTUAL_PLAYER_GATE_D1_CORE_REAL_SERVICE_TESTS"), True),
        "d1_adjacent_real_service": (
            recorder._read_makefile_paths("VIRTUAL_PLAYER_GATE_D1_ADJACENT_REAL_SERVICE_TESTS"),
            True,
        ),
        "gate_a_union": (
            recorder._read_makefile_paths("VIRTUAL_PLAYER_GATE_A_CONTRACT_TESTS")
            + recorder._read_makefile_paths("VIRTUAL_PLAYER_GATE_A_REAL_SERVICE_TESTS"),
            True,
        ),
    }
    paths = {"Makefile", *(path for files, _real in selections.values() for path in files)}

    def hashes():
        return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sorted(paths)}

    before = hashes()
    suites = {}
    for name, (files, real_services) in selections.items():
        collection = recorder._collect_suite(files, real_services=real_services)
        suites[name] = {
            **recorder._suite_collection_payload(collection),
            "collected_at_utc": collection.collected_at_utc,
            "real_services_configured": real_services,
        }
    if before != hashes():
        raise RuntimeError("Selected source files changed while collecting; regenerate after edits settle")
    payload = {
        "schema_version": 1,
        "record_type": "current_collection_only",
        "tests_executed": False,
        "historical_gate_evidence_updated": False,
        "production_certification": False,
        "runtime_activation_authorized": False,
        "source_file_sha256": before,
        "checksum_algorithm": "sha256",
        "checksum_input": "sorted_pytest_nodeids_joined_with_lf_and_terminal_lf",
        "encoding": "utf-8",
        "suites": suites,
    }
    args.output.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    print({name: row["expected_nodeid_count"] for name, row in suites.items()})


if __name__ == "__main__":
    main()
