from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from django.core.management.utils import get_random_secret_key
from django.db import migrations

PROJECT_ROOT = Path(__file__).resolve().parents[1]
READINESS_SCRIPT = PROJECT_ROOT / "scripts" / "check_web_readiness.py"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def _read_env_example() -> dict[str, str]:
    return _read_env_file(PROJECT_ROOT / ".env.example")


def _load_readiness_module():
    assert READINESS_SCRIPT.exists(), "readiness healthcheck script must exist"
    spec = importlib.util.spec_from_file_location("check_web_readiness", READINESS_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_settings_import(secret_key: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DJANGO_DEBUG": "0",
        "DJANGO_ALLOWED_HOSTS": "example.com",
        "DJANGO_SECRET_KEY": secret_key,
        "DJANGO_STRICT_INFRA_CONFIG": "0",
        "DJANGO_DB_ENGINE": "django.db.backends.sqlite3",
        "DJANGO_DB_NAME": ":memory:",
    }
    return subprocess.run(
        [sys.executable, "-c", "from config.settings import SECRET_KEY; print(SECRET_KEY)"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_readiness_request_uses_fixed_loopback_url_and_explicit_healthcheck_host() -> None:
    module = _load_readiness_module()

    request = module.build_readiness_request(
        {
            "DJANGO_HEALTHCHECK_HOST": "health.internal",
            "DJANGO_ALLOWED_HOSTS": "public.example,admin.example",
            "DJANGO_SECURE_SSL_REDIRECT": "0",
        }
    )

    assert request.full_url == "http://127.0.0.1:8000/health/ready"
    assert request.get_header("Host") == "health.internal"
    assert request.get_header("X-forwarded-proto") is None


def test_readiness_request_falls_back_to_first_allowed_host() -> None:
    module = _load_readiness_module()

    request = module.build_readiness_request(
        {
            "DJANGO_ALLOWED_HOSTS": "first.example, second.example",
            "DJANGO_SECURE_SSL_REDIRECT": "0",
        }
    )

    assert request.get_header("Host") == "first.example"


def test_readiness_request_marks_https_when_ssl_redirect_is_enabled() -> None:
    module = _load_readiness_module()

    request = module.build_readiness_request(
        {
            "DJANGO_ALLOWED_HOSTS": "example.com",
            "DJANGO_SECURE_SSL_REDIRECT": "1",
        }
    )

    assert request.get_header("X-forwarded-proto") == "https"


def test_readiness_main_uses_ten_second_timeout_and_accepts_only_2xx(monkeypatch) -> None:
    module = _load_readiness_module()
    calls = []

    class Response:
        def __init__(self, status: int) -> None:
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            return None

    def open_success(request, *, timeout):
        calls.append((request, timeout))
        return Response(204)

    class Opener:
        open = staticmethod(open_success)

    handlers = []

    def build_opener(handler):
        handlers.append(handler)
        return Opener()

    monkeypatch.setattr(module.urllib.request, "build_opener", build_opener)
    assert module.main({"DJANGO_ALLOWED_HOSTS": "example.com"}) == 0
    assert calls[0][1] == 10
    assert isinstance(handlers[0], module.NoRedirectHandler)

    Opener.open = staticmethod(lambda request, *, timeout: Response(302))
    with pytest.raises(RuntimeError, match="non-success status 302"):
        module.main({"DJANGO_ALLOWED_HOSTS": "example.com"})


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
    assert services["caddy"]["depends_on"]["web"]["condition"] == "service_healthy"
    web_healthcheck = services["web"]["healthcheck"]
    assert web_healthcheck["test"] == ["CMD", "python", "scripts/check_web_readiness.py"]
    assert web_healthcheck["timeout"] == "12s"


def test_prod_env_declares_explicit_healthcheck_host() -> None:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["DJANGO_HEALTHCHECK_HOST"] == "luanwu.top"


def test_prod_env_declares_caddy_and_websocket_capacity_defaults() -> None:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["CADDY_SITE_ADDRESS"] == "luanwu.top"
    assert values["DJANGO_WEBSOCKET_MAX_CONNECTIONS_PER_IP"] == "20"
    assert values["DJANGO_WEBSOCKET_HANDSHAKE_RATE_PER_SECOND"] == "10"
    assert values["DJANGO_WEBSOCKET_HANDSHAKE_BURST"] == "20"
    assert values["DJANGO_WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS"] == "120"


@pytest.mark.parametrize(
    "filename",
    [".env.example", ".env.docker.example", ".env.docker.prod.example"],
)
def test_env_examples_declare_worker_owned_websocket_capacity_defaults(filename: str) -> None:
    values: dict[str, str] = {}
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value

    assert values["DJANGO_WEBSOCKET_MAX_CONNECTIONS_PER_USER"] == "9"
    assert values["DJANGO_WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS"] == "30"
    assert values["DJANGO_WEBSOCKET_WORKER_LEASE_TTL_SECONDS"] == "8"
    assert values["DJANGO_WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS"] == "2"


def test_base_loads_reconnect_policy_before_authenticated_websocket_clients() -> None:
    template = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    policy_position = template.index("js/websocket_reconnect.js")

    assert policy_position < template.index("js/notifications.js")
    assert policy_position < template.index("js/online_stats.js")
    assert policy_position < template.index("js/chat_widget_connection.js")


@pytest.mark.parametrize(
    "secret_key",
    [
        "please-change-me-in-production",
        "x" * 49,
        "x" * 50,
        "django-insecure-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ",
    ],
)
def test_production_rejects_placeholder_or_short_secret_keys(secret_key: str) -> None:
    result = _run_settings_import(secret_key)

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY must be at least 50 characters" in result.stderr


def test_production_accepts_strong_secret_key() -> None:
    secret_key = get_random_secret_key()

    result = _run_settings_import(secret_key)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == secret_key


def test_blueprint_reward_claim_index_matches_migration_and_backend_name_limit() -> None:
    from guilds.models import GuildBlueprintRewardClaim

    create_migration = importlib.import_module("guilds.migrations.0021_guild_blueprint_reward_claim")
    rename_migration = importlib.import_module("guilds.migrations.0022_rename_blueprint_reward_claim_index")
    fields = ("member", "rarity", "claimed_at")
    model_indexes = {tuple(index.fields): index.name for index in GuildBlueprintRewardClaim._meta.indexes}
    create_indexes = {
        tuple(operation.index.fields): operation.index.name
        for operation in create_migration.Migration.operations
        if isinstance(operation, migrations.AddIndex)
    }
    rename_operations = [
        operation
        for operation in rename_migration.Migration.operations
        if isinstance(operation, migrations.RenameIndex)
    ]

    assert model_indexes[fields] == "guild_bp_member_rarity_idx"
    assert create_indexes[fields] == "guild_bp_claim_member_rarity_idx"
    assert len(rename_operations) == 1
    assert rename_operations[0].old_name == create_indexes[fields]
    assert rename_operations[0].new_name == model_indexes[fields]
    assert len(model_indexes[fields]) <= 30


def test_prod_runbook_compose_commands_load_env_docker_for_interpolation() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbook_deploy_docker.md").read_text(encoding="utf-8")

    assert 'docker compose --env-file ".env.docker" -f "docker-compose.prod.yml"' in runbook
    assert 'docker compose -f "docker-compose.prod.yml"' not in runbook


def test_prod_env_example_usage_loads_env_docker_for_compose_interpolation() -> None:
    env_example = (PROJECT_ROOT / ".env.docker.prod.example").read_text(encoding="utf-8")

    assert "docker compose --env-file .env.docker -f docker-compose.prod.yml up -d --build" in env_example


def test_prod_compose_uses_caddy_as_the_only_public_ingress() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "nginx" not in services
    assert "caddy" in services

    caddy = services["caddy"]
    assert caddy["image"] == "caddy:2.10-alpine"
    assert caddy["ports"] == ["80:80", "443:443", "443:443/udp"]
    assert caddy["environment"]["CADDY_SITE_ADDRESS"] == (
        "${CADDY_SITE_ADDRESS:?set CADDY_SITE_ADDRESS in .env.docker}"
    )
    assert caddy["depends_on"]["web"]["condition"] == "service_healthy"
    assert "./docker/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert "./runtime/staticfiles:/srv/staticfiles:ro" in caddy["volumes"]
    assert "./runtime/media:/srv/media:ro" in caddy["volumes"]
    assert "caddy_data:/data" in caddy["volumes"]
    assert "caddy_config:/config" in caddy["volumes"]
    assert "caddy_data" in compose["volumes"]
    assert "caddy_config" in compose["volumes"]

    for service_name in (
        "web",
        "worker",
        "worker_battle",
        "worker_timer",
        "worker_timer_scan",
        "worker_timer_maintenance",
        "beat",
    ):
        assert services[service_name]["env_file"] == "${WEBGAME_ENV_FILE:-.env.docker}"


def test_caddyfile_preserves_static_media_and_proxy_behavior() -> None:
    caddyfile = (PROJECT_ROOT / "docker" / "caddy" / "Caddyfile").read_text(encoding="utf-8")

    assert "{$CADDY_SITE_ADDRESS}" in caddyfile
    assert "encode zstd gzip" in caddyfile
    assert "request_body" in caddyfile
    assert "max_size 20MB" in caddyfile
    assert r"^/static/.+\.[0-9a-f]{12}\..+$" in caddyfile
    assert 'Cache-Control "public, max-age=31536000, immutable"' in caddyfile
    assert 'Cache-Control "public, max-age=3600"' in caddyfile
    assert 'Cache-Control "public, max-age=86400"' in caddyfile
    assert "root * /srv/staticfiles" in caddyfile
    assert "root * /srv/media" in caddyfile
    assert "reverse_proxy web:8000" in caddyfile
    assert "trusted_proxies" not in caddyfile


def test_nginx_deployment_artifact_is_removed() -> None:
    assert not (PROJECT_ROOT / "docker" / "nginx" / "default.conf").exists()


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


@pytest.mark.parametrize("filename", [".env.example", ".env.docker.example", ".env.docker.prod.example"])
def test_env_examples_define_split_timer_concurrencies(filename: str) -> None:
    values = _read_env_file(PROJECT_ROOT / filename)

    assert values["CELERY_TIMER_SCAN_CONCURRENCY"] == "2"
    assert values["CELERY_TIMER_MAINTENANCE_CONCURRENCY"] == "1"


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

    app_services = [
        "web",
        "worker",
        "worker_battle",
        "worker_timer",
        "worker_timer_scan",
        "worker_timer_maintenance",
        "beat",
    ]
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


def test_prod_runbook_documents_caddy_automatic_https() -> None:
    runbook = (PROJECT_ROOT / "docs" / "runbook_deploy_docker.md").read_text(encoding="utf-8")

    assert "Caddy" in runbook
    assert "自动申请和续期" in runbook
    assert "80/443" in runbook
    assert "caddy_data" in runbook
    assert "DJANGO_SECURE_SSL_REDIRECT=1" in runbook
    assert 'caddy validate --config "/etc/caddy/Caddyfile"' in runbook
