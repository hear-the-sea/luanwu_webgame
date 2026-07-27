from __future__ import annotations

import configparser
import os
import subprocess
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]


def test_pytest_testpaths_include_app_local_test_directories():
    config = configparser.ConfigParser()
    config.read(ROOT_DIR / "pytest.ini")

    testpaths = {line.strip() for line in config["pytest"]["testpaths"].splitlines() if line.strip()}

    assert {"tests", "guests/tests"}.issubset(testpaths)


def test_makefile_critical_gate_includes_arena_coop_concurrency_file():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_arena_coop_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_arena_virtual_population_concurrency_file():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_arena_virtual_population_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_arena_resolution_concurrency_file():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_arena_resolution_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_guild_raid_failure_concurrency_file():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_guild_raid_failure_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_trade_auction_concurrency_file():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_trade_auction_concurrency_integration.py" in makefile_content


def test_makefile_critical_gate_includes_guest_equipment_concurrency_file():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "tests/test_guest_equipment_concurrency_integration.py" in makefile_content


def test_makefile_real_service_gates_run_preflight_script():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "scripts/check_env_services_ready.py" in makefile_content


def test_makefile_exposes_real_service_dependency_lifecycle_targets():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "test-real-services-up:" in makefile_content
    assert "test-real-services-down:" in makefile_content
    assert "docker compose -f docker-compose.yml up -d db redis" in makefile_content
    assert "docker compose -f docker-compose.yml stop db redis" in makefile_content


def test_compose_publishes_real_service_ports_with_overridable_local_defaults():
    compose = yaml.safe_load((ROOT_DIR / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["db"]["ports"] == ["${REAL_SERVICES_MYSQL_PORT:-13306}:3306"]
    assert compose["services"]["redis"]["ports"] == ["${REAL_SERVICES_REDIS_PORT:-16379}:6379"]


def test_makefile_real_service_targets_inject_compose_matching_env():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "ifdef DJANGO_DB_PORT" in makefile_content
    assert "REAL_SERVICES_MYSQL_PORT ?= $(DJANGO_DB_PORT)" in makefile_content
    assert "REAL_SERVICES_MYSQL_PORT ?= 13306" in makefile_content
    assert "DJANGO_DB_PORT ?= $(REAL_SERVICES_MYSQL_PORT)" in makefile_content
    assert "REAL_SERVICES_REDIS_PORT ?= 16379" in makefile_content
    assert "DJANGO_DB_ENGINE=django.db.backends.mysql" in makefile_content
    assert "DJANGO_DB_HOST=$(DJANGO_DB_HOST)" in makefile_content
    assert "DJANGO_DB_PORT=$(DJANGO_DB_PORT)" in makefile_content
    assert "DJANGO_DB_USER=$(DJANGO_DB_USER)" in makefile_content
    assert "DJANGO_DB_PASSWORD=$(DJANGO_DB_PASSWORD)" in makefile_content
    assert "DJANGO_DB_NAME=$(DJANGO_DB_NAME)" in makefile_content
    assert "REDIS_URL=$(REDIS_URL)" in makefile_content
    assert "REDIS_CACHE_URL=$(REDIS_CACHE_URL)" in makefile_content
    assert "REDIS_PASSWORD=$(REDIS_PASSWORD)" in makefile_content
    assert "$(REAL_SERVICE_TEST_ENV) $(MAKE) test-real-services-preflight" in makefile_content


def test_makefile_real_service_targets_are_dry_run_parseable():
    result = subprocess.run(
        ["make", "-n", "test-real-services-preflight"],
        cwd=ROOT_DIR,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "DJANGO_DB_PORT=13306" in result.stdout
    assert "REDIS_CACHE_URL=redis://127.0.0.1:16379/2" in result.stdout


def test_makefile_exposes_javascript_quality_gate():
    makefile_content = (ROOT_DIR / "Makefile").read_text(encoding="utf-8")

    assert "lint-js:" in makefile_content
    assert "npm run check:js" in makefile_content
    assert "npm run test:js" in makefile_content
    assert "lint: lint-js" in makefile_content
