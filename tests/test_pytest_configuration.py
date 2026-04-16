from __future__ import annotations

import configparser
from pathlib import Path


def test_pytest_testpaths_include_app_local_test_directories():
    config = configparser.ConfigParser()
    config.read(Path(__file__).resolve().parents[1] / "pytest.ini")

    testpaths = {line.strip() for line in config["pytest"]["testpaths"].splitlines() if line.strip()}

    assert {"tests", "guests/tests"}.issubset(testpaths)


def test_makefile_critical_gate_includes_arena_coop_concurrency_file():
    makefile_content = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_arena_coop_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_trade_auction_concurrency_file():
    makefile_content = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_trade_auction_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_guest_equipment_concurrency_file():
    makefile_content = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_guest_equipment_concurrency_integration.py" in makefile_content


def test_makefile_real_service_gates_run_preflight_script():
    makefile_content = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")

    assert "scripts/check_env_services_ready.py" in makefile_content
