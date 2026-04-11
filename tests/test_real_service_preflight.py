from __future__ import annotations

import importlib.util
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


def test_build_redis_probe_uses_cache_url_endpoint():
    probe = preflight.build_redis_probe(
        {
            "REDIS_CACHE_URL": "redis://:secret@redis.internal:6380/2",
        }
    )

    assert probe.name == "redis"
    assert probe.endpoint == "redis.internal:6380"
    assert probe.command == ["redis-cli", "-u", "redis://:secret@redis.internal:6380/2", "ping"]
