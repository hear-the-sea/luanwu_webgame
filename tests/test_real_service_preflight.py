from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_env_services_ready.py"
_SPEC = importlib.util.spec_from_file_location("check_env_services_ready", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
preflight = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = preflight
_SPEC.loader.exec_module(preflight)


def test_build_mysql_probe_uses_configured_database_endpoint():
    probe = preflight.build_mysql_probe(
        {
            "DJANGO_DB_HOST": "db.internal",
            "DJANGO_DB_PORT": "3307",
            "DJANGO_DB_USER": "webgame",
            "DJANGO_DB_PASSWORD": "secret",
        }
    )

    assert probe.name == "mysql"
    assert probe.endpoint == "db.internal:3307"
    assert probe.command == ["mysqladmin", "ping", "-h", "db.internal", "-P", "3307", "-u", "webgame"]
    assert probe.env_overrides == {"MYSQL_PWD": "secret"}
    assert probe.timeout_seconds == 3


def test_build_redis_probe_uses_env_auth_without_password_in_command():
    probe = preflight.build_redis_probe(
        {
            "REDIS_CACHE_URL": "redis://:secret@redis.internal:6380/2",
        }
    )

    assert probe.name == "redis"
    assert probe.endpoint == "redis.internal:6380"
    assert probe.command == ["redis-cli", "-h", "redis.internal", "-p", "6380", "-n", "2", "ping"]
    assert probe.env_overrides == {"REDISCLI_AUTH": "secret"}
    assert probe.timeout_seconds == 3
    assert "secret" not in " ".join(probe.command)


def test_run_probe_reports_timeout():
    probe = preflight.ServiceProbe(
        name="redis",
        endpoint="redis.internal:6380",
        command=["redis-cli", "ping"],
        env_overrides={},
        timeout_seconds=2,
    )

    def _runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs["args"] if "args" in kwargs else args[0], timeout=2)

    failure = preflight.run_probe(probe, runner=_runner)

    assert failure == "redis preflight timed out at redis.internal:6380 after 2s"


def test_format_real_service_start_hint_points_to_lifecycle_targets_without_passwords():
    hint = preflight.format_real_service_start_hint()

    assert "make test-real-services-up" in hint
    assert "DJANGO_TEST_USE_ENV_SERVICES=1 make test-real-services" in hint
    assert "make test-real-services-down" in hint
    assert "password" not in hint.lower()
    assert "secret" not in hint


def test_main_failure_output_points_to_lifecycle_targets_without_real_services(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight,
        "check_env_services_ready",
        lambda: ["mysql preflight failed at db.internal:3306: exit=1"],
    )

    exit_code = preflight.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "real-services preflight failed:" in captured.err
    assert "mysql preflight failed at db.internal:3306: exit=1" in captured.err
    assert "make test-real-services-up" in captured.err
    assert "DJANGO_TEST_USE_ENV_SERVICES=1 make test-real-services" in captured.err
    assert "make test-real-services-down" in captured.err
    assert "secret" not in captured.err
