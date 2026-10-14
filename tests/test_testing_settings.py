from __future__ import annotations

import importlib
import os
import tempfile

import pytest

from config.celery import app as celery_app
from config.settings import testing as testing_settings


def _reload_testing_settings(
    monkeypatch: pytest.MonkeyPatch,
    eager: str | None = None,
    celery_env: dict[str, str] | None = None,
):
    if eager is None:
        monkeypatch.delenv("DJANGO_TEST_CELERY_EAGER", raising=False)
    else:
        monkeypatch.setenv("DJANGO_TEST_CELERY_EAGER", eager)

    for key, value in (celery_env or {}).items():
        monkeypatch.setenv(key, value)

    return importlib.reload(testing_settings)


@pytest.fixture(autouse=True)
def _restore_testing_settings(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.delenv("DJANGO_TEST_CELERY_EAGER", raising=False)
    for key in testing_settings.CELERY_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(testing_settings)


def test_testing_settings_force_in_memory_backends(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    module = _reload_testing_settings(monkeypatch)

    assert module.DATABASES == {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.path.join(tempfile.gettempdir(), f"web_game_v5_test_main_{os.getpid()}.sqlite3"),
        }
    }
    assert module.CACHES["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert module.CHANNEL_LAYERS["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"
    assert module.HEALTH_CHECK_REQUIRE_INTERNAL is False
    assert module.HEALTH_CHECK_CHANNEL_LAYER is False
    assert module.HEALTH_CHECK_CACHE_TTL_SECONDS == 0
    assert module.HEALTH_CHECK_INCLUDE_DETAILS is False
    assert module.HEALTH_CHECK_CELERY_BROKER is False
    assert module.HEALTH_CHECK_CELERY_WORKERS is False
    assert module.HEALTH_CHECK_CELERY_BEAT is False
    assert module.HEALTH_CHECK_CELERY_ROUNDTRIP is False
    assert module.SINGLE_SESSION_FAIL_OPEN is False


def test_testing_settings_uses_xdist_worker_in_sqlite_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    module = _reload_testing_settings(monkeypatch)

    assert module.DATABASES["default"]["NAME"] == os.path.join(
        tempfile.gettempdir(),
        f"web_game_v5_test_gw3_{os.getpid()}.sqlite3",
    )


def test_testing_settings_clear_external_celery_env(monkeypatch: pytest.MonkeyPatch):
    module = _reload_testing_settings(
        monkeypatch,
        celery_env={key: f"test-{index}" for index, key in enumerate(testing_settings.CELERY_ENV_VARS_TO_CLEAR)},
    )

    assert module.CELERY_BROKER_URL == "memory://"
    assert module.CELERY_RESULT_BACKEND == "cache+memory://"
    for key in module.CELERY_ENV_VARS_TO_CLEAR:
        assert key not in os.environ


def test_testing_settings_sync_celery_app_config(monkeypatch: pytest.MonkeyPatch):
    module = _reload_testing_settings(monkeypatch, eager="true")

    assert str(celery_app.conf.broker_url) == module.CELERY_BROKER_URL
    assert str(celery_app.conf.result_backend) == module.CELERY_RESULT_BACKEND
    assert celery_app.conf.task_always_eager is True
    assert celery_app.conf.task_eager_propagates is True


def test_testing_settings_default_celery_not_eager(monkeypatch: pytest.MonkeyPatch):
    module = _reload_testing_settings(monkeypatch, eager=None)

    assert module.CELERY_TASK_ALWAYS_EAGER is False
    assert module.CELERY_TASK_EAGER_PROPAGATES is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_testing_settings_celery_eager_can_be_enabled(monkeypatch: pytest.MonkeyPatch, value: str):
    module = _reload_testing_settings(monkeypatch, eager=value)

    assert module.CELERY_TASK_ALWAYS_EAGER is True
    assert module.CELERY_TASK_EAGER_PROPAGATES is True
