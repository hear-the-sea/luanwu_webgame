import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MYSQL_TEST_IMAGE = "mysql:8.4.10"
CHECK_CSS_COMMAND = "npm run build:css && git diff --exit-code -- static/css/tailwind.css"
MIGRATION_CHECK_COMMAND = "python manage.py makemigrations --check --dry-run"
MIGRATION_CHECK_ENV = {
    "DJANGO_DEBUG": "0",
    "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "DJANGO_SECRET_KEY": "ci-only-not-a-real-secret-key-change-me-1234567890",
    "DJANGO_STRICT_INFRA_CONFIG": "0",
    "DJANGO_DB_ENGINE": "django.db.backends.sqlite3",
    "DJANGO_DB_NAME": ":memory:",
}


def test_real_service_compose_pins_supported_mysql_and_configurable_root_password():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    database = compose["services"]["db"]

    assert database["image"] == MYSQL_TEST_IMAGE
    assert "command" not in database
    assert database["environment"]["MYSQL_ROOT_PASSWORD"] == "${DJANGO_DB_ROOT_PASSWORD:-root}"


def test_real_service_gate_uses_dedicated_database_creation_credentials():
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assignments = {
        line.split("=", 1)[0].strip(): line
        for line in makefile.splitlines()
        if "=" in line and not line.startswith("\t")
    }

    assert assignments["REAL_SERVICES_TEST_DB_USER ?"] == "REAL_SERVICES_TEST_DB_USER ?= root"
    assert assignments["REAL_SERVICES_TEST_DB_PASSWORD ?"] == (
        "REAL_SERVICES_TEST_DB_PASSWORD ?= $(DJANGO_DB_ROOT_PASSWORD)"
    )
    assert assignments["REAL_SERVICE_TEST_ENV"].count("DJANGO_DB_USER=$(REAL_SERVICES_TEST_DB_USER)") == 1
    assert assignments["REAL_SERVICE_TEST_ENV"].count("DJANGO_DB_PASSWORD=$(REAL_SERVICES_TEST_DB_PASSWORD)") == 1
    assert "DJANGO_DB_ROOT_PASSWORD=$(DJANGO_DB_ROOT_PASSWORD)" in assignments["REAL_SERVICE_COMPOSE_ENV"]


def test_integration_ci_uses_database_creation_credentials():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    integration_job = workflow["jobs"]["integration-tests"]
    mysql_environment = integration_job["services"]["mysql"]["env"]
    integration_environment = integration_job["env"]

    assert integration_environment["DJANGO_DB_USER"] == "root"
    assert integration_environment["DJANGO_DB_PASSWORD"] == mysql_environment["MYSQL_ROOT_PASSWORD"]
    assert mysql_environment["MYSQL_DATABASE"] == "webgame"
    assert integration_environment["DJANGO_DB_NAME"] == "webgame"
    assert integration_environment["DJANGO_STRICT_INFRA_CONFIG"] == "0"
    assert integration_environment["DJANGO_SECURE_SSL_REDIRECT"] == "0"


def test_integration_ci_partitions_general_virtual_player_and_capacity_suites():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    integration_job = workflow["jobs"]["integration-tests"]
    matrix = integration_job["strategy"]["matrix"]["include"]

    assert integration_job["strategy"]["fail-fast"] is False
    assert integration_job["timeout-minutes"] == 60
    assert {entry["suite"] for entry in matrix} == {"general", "virtual-player", "capacity"}
    selectors = {entry["suite"]: (entry["pytest_marker"], entry["pytest_keyword"]) for entry in matrix}
    assert selectors == {
        "general": ("integration and not capacity", "not virtual_player"),
        "virtual-player": ("integration and not capacity", "virtual_player"),
        "capacity": ("integration and capacity", ""),
    }


def test_mypy_ci_loads_non_production_django_settings():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    type_check_step = next(
        step for step in workflow["jobs"]["tests"]["steps"] if step.get("name") == "Type check (mypy)"
    )

    assert type_check_step["env"] == {
        "DJANGO_DEBUG": "0",
        "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "DJANGO_SECRET_KEY": "ci-only-not-a-real-secret-key-change-me-1234567890",
        "DJANGO_STRICT_INFRA_CONFIG": "0",
    }


def test_unit_ci_disables_https_redirect_for_http_client_tests():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    unit_step = next(step for step in workflow["jobs"]["tests"]["steps"] if step.get("name") == "Unit Tests (pytest)")

    assert unit_step["env"]["DJANGO_SECURE_SSL_REDIRECT"] == "0"
    assert "not integration and not evidence" in unit_step["run"]


def test_package_script_builds_css_and_rejects_artifact_drift():
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"].get("check:css") == CHECK_CSS_COMMAND


def test_unit_ci_checks_css_after_installing_node_dependencies():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    run_commands = [step.get("run") for step in workflow["jobs"]["tests"]["steps"]]

    assert "npm ci" in run_commands
    assert "npm run check:css" in run_commands

    assert run_commands.index("npm ci") < run_commands.index("npm run check:css")


def test_unit_ci_checks_for_uncommitted_migrations_after_dependencies():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["tests"]["steps"]
    migration_check_steps = [step for step in steps if step.get("run") == MIGRATION_CHECK_COMMAND]

    assert len(migration_check_steps) == 1
    assert migration_check_steps[0].get("env") == MIGRATION_CHECK_ENV

    install_dependencies_index = next(
        index for index, step in enumerate(steps) if step.get("name") == "Install dependencies"
    )
    migration_check_index = steps.index(migration_check_steps[0])

    assert install_dependencies_index < migration_check_index
