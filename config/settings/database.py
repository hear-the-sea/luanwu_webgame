"""
Database and cache configuration.
"""

from __future__ import annotations

from urllib.parse import quote, urlparse, urlunparse

from .base import BASE_DIR, DEBUG, RUNNING_TESTS, env, env_float

# Redis configuration
REDIS_URL = env("REDIS_URL", "redis://127.0.0.1:6379")
REDIS_PASSWORD = env("REDIS_PASSWORD", "")


def _redis_url_with_password(url: str, password: str) -> str:
    """Inject password into a redis:// URL if it has no auth part."""
    if not password:
        return url

    parsed = urlparse(url)
    if parsed.scheme not in {"redis", "rediss"}:
        return url

    if "@" in (parsed.netloc or ""):
        return url

    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    auth = f":{quote(password, safe='')}@"
    netloc = f"{auth}{host}{port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _redis_url_has_auth(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.username or parsed.password or ("@" in (parsed.netloc or "")))


def _validate_production_infrastructure(
    *,
    strict_mode: bool,
    db_engine: str,
    db_name: str,
    redis_password: str,
    redis_urls: tuple[str, ...],
) -> None:
    if not strict_mode:
        return

    normalized_engine = str(db_engine or "").strip().lower()
    normalized_db_name = str(db_name or "").strip()
    if not normalized_engine or normalized_engine == "django.db.backends.sqlite3":
        raise RuntimeError(
            "Production requires an explicit non-SQLite database configuration. "
            "Set DJANGO_DB_ENGINE and related database environment variables."
        )
    if not normalized_db_name:
        raise RuntimeError("Production requires DJANGO_DB_NAME to be set.")

    if not redis_password and any(
        urlparse(url).scheme in {"redis", "rediss"} and not _redis_url_has_auth(url) for url in redis_urls
    ):
        raise RuntimeError(
            "Production requires authenticated Redis configuration. "
            "Set REDIS_PASSWORD or embed credentials in each Redis URL."
        )


REDIS_BROKER_URL = _redis_url_with_password(env("REDIS_BROKER_URL", f"{REDIS_URL}/0"), REDIS_PASSWORD)
REDIS_RESULT_URL = _redis_url_with_password(env("REDIS_RESULT_URL", REDIS_BROKER_URL), REDIS_PASSWORD)
REDIS_CHANNEL_URL = _redis_url_with_password(env("REDIS_CHANNEL_URL", f"{REDIS_URL}/1"), REDIS_PASSWORD)
REDIS_CACHE_URL = _redis_url_with_password(env("REDIS_CACHE_URL", f"{REDIS_URL}/2"), REDIS_PASSWORD)

# Redis connection budgets. Keep every Redis client bounded so an outage cannot
# turn into an unbounded wait in a request, WebSocket, or background task.
REDIS_SOCKET_CONNECT_TIMEOUT = max(0.1, env_float("REDIS_SOCKET_CONNECT_TIMEOUT", 2.0))
REDIS_SOCKET_TIMEOUT = max(0.1, env_float("REDIS_SOCKET_TIMEOUT", 2.0))
# channels_redis uses a five-second blocking receive by default. Keep this
# timeout above that interval so an idle WebSocket is not mistaken for a
# broken Redis connection while still bounding a genuinely stalled read.
REDIS_CHANNEL_SOCKET_TIMEOUT = max(6.0, env_float("REDIS_CHANNEL_SOCKET_TIMEOUT", 10.0))

# Redis cache configuration
_redis_cache_options = {
    "CLIENT_CLASS": "django_redis.client.DefaultClient",
    "SOCKET_CONNECT_TIMEOUT": REDIS_SOCKET_CONNECT_TIMEOUT,
    "SOCKET_TIMEOUT": REDIS_SOCKET_TIMEOUT,
}
if REDIS_PASSWORD:
    _redis_cache_options["PASSWORD"] = REDIS_PASSWORD

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "OPTIONS": _redis_cache_options,
    }
}

# Database engine configuration
_db_engine = env("DJANGO_DB_ENGINE", "django.db.backends.sqlite3")
_db_name = str(env("DJANGO_DB_NAME", str(BASE_DIR / "db.sqlite3")))
_strict_infra_config = env("DJANGO_STRICT_INFRA_CONFIG", "1" if not DEBUG and not RUNNING_TESTS else "0") == "1"

# Connection pool configuration
_conn_max_age_str = env("DJANGO_DB_CONN_MAX_AGE", "")
if _conn_max_age_str.lower() == "none":
    _conn_max_age = None
elif _conn_max_age_str:
    _conn_max_age = int(_conn_max_age_str)
else:
    # Keep the default bounded in production as well. A single-host MySQL
    # deployment has no external pooler, so a five-minute idle connection can
    # exhaust the small server-side pool during traffic spikes.
    _conn_max_age = 60

# Database-specific options
_db_connect_timeout = max(1, int(env_float("DJANGO_DB_CONNECT_TIMEOUT", 5.0)))
_db_read_timeout = max(1, int(env_float("DJANGO_DB_READ_TIMEOUT", 60.0)))
_db_write_timeout = max(1, int(env_float("DJANGO_DB_WRITE_TIMEOUT", 60.0)))
_db_options = {}
if "postgresql" in _db_engine:
    _db_options = {
        "connect_timeout": _db_connect_timeout,
        "application_name": "webgame_v5",
        "sslmode": env("DJANGO_DB_SSLMODE", "prefer"),
    }
elif "mysql" in _db_engine:
    _db_options = {
        "connect_timeout": _db_connect_timeout,
        "read_timeout": _db_read_timeout,
        "write_timeout": _db_write_timeout,
        "init_command": (
            "SET sql_mode='STRICT_TRANS_TABLES', "
            "character_set_client='utf8mb4', "
            "character_set_connection='utf8mb4', "
            "character_set_results='utf8mb4', "
            "collation_connection='utf8mb4_unicode_ci'"
        ),
    }

DATABASES = {
    "default": {
        "ENGINE": _db_engine,
        "NAME": _db_name,
        "USER": env("DJANGO_DB_USER", ""),
        "PASSWORD": env("DJANGO_DB_PASSWORD", ""),
        "HOST": env("DJANGO_DB_HOST", ""),
        "PORT": env("DJANGO_DB_PORT", ""),
        "CONN_MAX_AGE": _conn_max_age,
        "CONN_HEALTH_CHECKS": env("DJANGO_DB_CONN_HEALTH_CHECKS", "1") == "1",
        "DISABLE_SERVER_SIDE_CURSORS": env("DJANGO_DB_DISABLE_SERVER_SIDE_CURSORS", "") == "1",
        "OPTIONS": _db_options,
    }
}

_validate_production_infrastructure(
    strict_mode=_strict_infra_config,
    db_engine=_db_engine,
    db_name=_db_name,
    redis_password=REDIS_PASSWORD,
    redis_urls=(REDIS_URL, REDIS_BROKER_URL, REDIS_RESULT_URL, REDIS_CHANNEL_URL, REDIS_CACHE_URL),
)

# Channel layers
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "address": REDIS_CHANNEL_URL,
                    "socket_connect_timeout": REDIS_SOCKET_CONNECT_TIMEOUT,
                    "socket_timeout": REDIS_CHANNEL_SOCKET_TIMEOUT,
                }
            ]
        },
    },
}
