from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def test_prod_compose_passes_redis_password_to_redis_container() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    redis_environment = compose["services"]["redis"].get("environment") or {}

    assert redis_environment["REDIS_PASSWORD"] == "${REDIS_PASSWORD:-}"


def test_prod_runbook_compose_commands_load_env_docker_for_interpolation() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbook_deploy_docker.md").read_text(encoding="utf-8")

    assert 'docker compose --env-file ".env.docker" -f "docker-compose.prod.yml"' in runbook
    assert 'docker compose -f "docker-compose.prod.yml"' not in runbook


def test_prod_env_example_usage_loads_env_docker_for_compose_interpolation() -> None:
    env_example = (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8")

    assert "docker compose --env-file .env.docker -f docker-compose.prod.yml up -d --build" in env_example


def test_dockerfile_installs_locked_runtime_requirements() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY requirements.lock.txt /app/requirements.lock.txt" in dockerfile
    assert "pip install --no-cache-dir -r /app/requirements.lock.txt" in dockerfile


def test_env_example_uses_local_development_defaults() -> None:
    values = _read_env_example()

    assert values["DJANGO_DEBUG"] == "1"
    assert values["DJANGO_DB_ENGINE"] == "django.db.backends.sqlite3"
    assert values["DJANGO_DB_NAME"] == "db.sqlite3"
    assert values["DJANGO_SECURE_SSL_REDIRECT"] == "0"


def test_prod_env_enables_full_async_readiness_checks() -> None:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["DJANGO_HEALTH_CHECK_CELERY_BROKER"] == "1"
    assert values["DJANGO_HEALTH_CHECK_CELERY_WORKERS"] == "1"
    assert values["DJANGO_HEALTH_CHECK_CELERY_BEAT"] == "1"
    assert values["DJANGO_HEALTH_CHECK_CELERY_ROUNDTRIP"] == "1"
