"""
Test environment overrides.
"""

from __future__ import annotations

import logging
import os
import tempfile

from django.core.exceptions import AppRegistryNotReady

logger = logging.getLogger(__name__)
TEST_GATE_MODE = "hermetic"
TEST_GATE_DESCRIPTION = "Hermetic rapid gate (SQLite, LocMem cache, InMemoryChannelLayer, memory Celery)."


CELERY_ENV_VARS_TO_CLEAR = (
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "CELERY_BROKER_READ_URL",
    "CELERY_BROKER_WRITE_URL",
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _using_env_services() -> bool:
    return _env_flag("DJANGO_TEST_USE_ENV_SERVICES", default=False)


def _clear_celery_env_vars() -> None:
    for key in CELERY_ENV_VARS_TO_CLEAR:
        os.environ.pop(key, None)


def _default_sqlite_test_db_path() -> str:
    worker_id = str(os.environ.get("PYTEST_XDIST_WORKER", "main") or "main")
    return os.path.join(tempfile.gettempdir(), f"web_game_v5_test_{worker_id}_{os.getpid()}.sqlite3")


_SQLITE_TEST_DB_PATH = _default_sqlite_test_db_path()

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_TEST_SQLITE_NAME", _SQLITE_TEST_DB_PATH),
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Force in-memory broker/backend during hermetic test runs.
_clear_celery_env_vars()

CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = _env_flag("DJANGO_TEST_CELERY_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = CELERY_TASK_ALWAYS_EAGER
HEALTH_CHECK_REQUIRE_INTERNAL = False
HEALTH_CHECK_CHANNEL_LAYER = False
HEALTH_CHECK_CACHE_TTL_SECONDS = 0
HEALTH_CHECK_INCLUDE_DETAILS = False
HEALTH_CHECK_CELERY_BROKER = False
HEALTH_CHECK_CELERY_WORKERS = False
HEALTH_CHECK_CELERY_BEAT = False
HEALTH_CHECK_CELERY_ROUNDTRIP = False
SINGLE_SESSION_FAIL_OPEN = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

if not _using_env_services():
    try:
        from config.celery import app as celery_app

        celery_app.conf.update(
            CELERY_BROKER_URL=CELERY_BROKER_URL,
            CELERY_RESULT_BACKEND=CELERY_RESULT_BACKEND,
            CELERY_TASK_ALWAYS_EAGER=CELERY_TASK_ALWAYS_EAGER,
            CELERY_TASK_EAGER_PROPAGATES=CELERY_TASK_EAGER_PROPAGATES,
            broker_url=CELERY_BROKER_URL,
            result_backend=CELERY_RESULT_BACKEND,
            task_always_eager=CELERY_TASK_ALWAYS_EAGER,
            task_eager_propagates=CELERY_TASK_EAGER_PROPAGATES,
        )
    except (ImportError, AppRegistryNotReady, OSError, RuntimeError):
        logger.warning("Failed to update Celery app for tests", exc_info=True)
