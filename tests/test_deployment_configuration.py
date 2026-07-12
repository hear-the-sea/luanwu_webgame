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

    assert redis_environment["REDIS_PASSWORD"] == "${REDIS_PASSWORD:?set REDIS_PASSWORD in .env.docker}"


def test_prod_env_requires_a_non_empty_redis_password() -> None:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["REDIS_PASSWORD"] == "change-me-strong-redis-password"


def test_prod_compose_waits_for_dependency_and_application_readiness() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "healthcheck" in services["redis"]
    assert "healthcheck" in services["db"]
    assert "healthcheck" in services["web"]
    assert services["web"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert services["web"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["nginx"]["depends_on"]["web"]["condition"] == "service_healthy"
    web_healthcheck = services["web"]["healthcheck"]
    healthcheck_command = " ".join(web_healthcheck["test"])
    assert "/health/ready" in healthcheck_command
    assert "timeout=10" in healthcheck_command
    assert web_healthcheck["timeout"] == "12s"


def test_prod_runbook_compose_commands_load_env_docker_for_interpolation() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbook_deploy_docker.md").read_text(encoding="utf-8")

    assert 'docker compose --env-file ".env.docker" -f "docker-compose.prod.yml"' in runbook
    assert 'docker compose -f "docker-compose.prod.yml"' not in runbook


def test_prod_env_example_usage_loads_env_docker_for_compose_interpolation() -> None:
    env_example = (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8")

    assert "docker compose --env-file .env.docker -f docker-compose.prod.yml up -d --build" in env_example


def test_nginx_health_proxy_forwards_client_ip_for_internal_health_gate() -> None:
    nginx_conf = (PROJECT_ROOT / "docker" / "nginx" / "default.conf").read_text(encoding="utf-8")
    health_block = nginx_conf.split("location /health/ {", 1)[1].split("}", 1)[0]

    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in health_block


def test_nginx_trusts_forwarded_proto_only_from_private_proxy_ranges() -> None:
    nginx_conf = (PROJECT_ROOT / "docker" / "nginx" / "default.conf").read_text(encoding="utf-8")

    assert "geo $django_trusted_forwarded_proxy" in nginx_conf
    assert "127.0.0.1/32 1;" in nginx_conf
    assert "10.0.0.0/8 1;" in nginx_conf
    assert "172.16.0.0/12 1;" in nginx_conf
    assert "192.168.0.0/16 1;" in nginx_conf
    assert 'map "$django_trusted_forwarded_proxy:$http_x_forwarded_proto" $django_forwarded_proto' in nginx_conf
    assert "~*^1:https$ https;" in nginx_conf
    assert 'default "$scheme";' in nginx_conf

    for location in ("location /health/ {", "location /ws/ {", "location / {"):
        block = nginx_conf.split(location, 1)[1].split("}", 1)[0]
        assert "proxy_set_header X-Forwarded-Proto $django_forwarded_proto;" in block


def test_prod_env_does_not_auto_sync_deleting_guest_templates_on_web_startup() -> None:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["DJANGO_SYNC_GUEST_TEMPLATES"] == "0"


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


def test_prod_runbook_chowns_runtime_dirs_for_appuser_collectstatic() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbook_deploy_docker.md").read_text(encoding="utf-8")

    assert 'chown -R "10001:10001" "runtime/staticfiles" "runtime/media" "runtime/celerybeat"' in runbook
    assert "read_only: true" in runbook
    assert "DJANGO_COLLECTSTATIC=1" in runbook
    assert "/app/staticfiles" in runbook


def test_prod_collectstatic_only_runs_where_staticfiles_volume_is_writable() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    app_services = ["web", "worker", "worker_battle", "worker_timer", "beat"]
    for service_name in app_services:
        service = compose["services"][service_name]
        volumes = service.get("volumes") or []
        has_staticfiles_volume = any(
            ":".join(volume.split(":")[:2]) == "./runtime/staticfiles:/app/staticfiles" for volume in volumes
        )
        environment = service.get("environment") or {}

        if has_staticfiles_volume:
            continue

        assert environment["DJANGO_COLLECTSTATIC"] == "0", service_name


def test_prod_runbook_documents_nginx_80_only_requires_outer_tls_termination() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbook_deploy_docker.md").read_text(encoding="utf-8")

    assert "Nginx" in runbook
    assert "80" in runbook
    assert "DJANGO_SECURE_SSL_REDIRECT=1" in runbook
    assert "TLS" in runbook
    assert "终止" in runbook
