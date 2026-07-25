from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_CONFTST_PATH = Path(__file__).with_name("conftest.py")
_SPEC = importlib.util.spec_from_file_location("_repo_conftest", _CONFTST_PATH)
assert _SPEC is not None and _SPEC.loader is not None
conftest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(conftest)


def test_require_external_cache_backend_skips_locmem():
    settings = SimpleNamespace(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})

    with pytest.raises(pytest.skip.Exception, match="external cache backend"):
        conftest._require_external_cache_backend(settings, cache=SimpleNamespace(), strict=False)


def test_require_external_channel_layer_skips_inmemory_backend():
    settings = SimpleNamespace(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})

    with pytest.raises(pytest.skip.Exception, match="external channel layer backend"):
        conftest._require_external_channel_layer(settings, channel_layer=SimpleNamespace(), strict=False)


def test_require_external_celery_broker_skips_memory_broker():
    celery_app = SimpleNamespace(conf=SimpleNamespace(broker_url="memory://", result_backend="cache+memory://"))

    with pytest.raises(pytest.skip.Exception, match="external Celery broker"):
        conftest._require_external_celery_broker(celery_app, strict=False)


def test_require_external_celery_broker_accepts_reachable_connection():
    ensured = {"count": 0}

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ensure_connection(self, max_retries: int = 1):
            ensured["count"] += 1
            assert max_retries == 1

    class _CeleryApp:
        conf = SimpleNamespace(
            broker_url="redis://127.0.0.1:6379/0",
            result_backend="redis://127.0.0.1:6379/1",
        )

        def connection(self):
            return _Connection()

    conftest._require_external_celery_broker(_CeleryApp(), strict=False)

    assert ensured["count"] == 1


def test_integration_service_guard_requests_env_services_for_integration_item():
    requested_fixtures = []
    request = SimpleNamespace(
        node=SimpleNamespace(get_closest_marker=lambda name: object() if name == "integration" else None),
        getfixturevalue=requested_fixtures.append,
    )

    conftest._guard_integration_services.__wrapped__(request)

    assert requested_fixtures == ["require_env_services"]


def test_integration_service_guard_ignores_non_integration_item():
    requested_fixtures = []
    request = SimpleNamespace(
        node=SimpleNamespace(get_closest_marker=lambda name: None),
        getfixturevalue=requested_fixtures.append,
    )

    conftest._guard_integration_services.__wrapped__(request)

    assert requested_fixtures == []


def test_should_fail_for_missing_env_services_when_only_integration_items_selected(monkeypatch):
    monkeypatch.delenv("DJANGO_TEST_USE_ENV_SERVICES", raising=False)

    class _Item:
        def __init__(self, integration: bool):
            self.integration = integration

        def get_closest_marker(self, name: str):
            if name == "integration" and self.integration:
                return object()
            return None

    config = SimpleNamespace(option=SimpleNamespace(markexpr=""))
    items = [_Item(True), _Item(True)]

    assert conftest._should_fail_for_missing_env_services(config, items) is True


def test_should_not_fail_for_missing_env_services_when_selection_is_mixed(monkeypatch):
    monkeypatch.delenv("DJANGO_TEST_USE_ENV_SERVICES", raising=False)

    class _Item:
        def __init__(self, integration: bool):
            self.integration = integration

        def get_closest_marker(self, name: str):
            if name == "integration" and self.integration:
                return object()
            return None

    config = SimpleNamespace(option=SimpleNamespace(markexpr=""))
    items = [_Item(True), _Item(False)]

    assert conftest._should_fail_for_missing_env_services(config, items) is False


def test_should_fail_for_missing_env_services_when_markexpr_requests_integration(monkeypatch):
    monkeypatch.delenv("DJANGO_TEST_USE_ENV_SERVICES", raising=False)

    class _Item:
        def __init__(self, integration: bool):
            self.integration = integration

        def get_closest_marker(self, name: str):
            if name == "integration" and self.integration:
                return object()
            return None

    config = SimpleNamespace(option=SimpleNamespace(markexpr="integration"))
    items = [_Item(True), _Item(False)]

    assert conftest._should_fail_for_missing_env_services(config, items) is True


@pytest.mark.parametrize(
    "markexpr",
    [
        "not integration",
        "smoke and not integration",
        "not (integration or smoke)",
    ],
)
def test_should_not_fail_for_missing_env_services_when_markexpr_excludes_integration(monkeypatch, markexpr: str):
    monkeypatch.delenv("DJANGO_TEST_USE_ENV_SERVICES", raising=False)

    class _Item:
        def __init__(self, integration: bool):
            self.integration = integration

        def get_closest_marker(self, name: str):
            if name == "integration" and self.integration:
                return object()
            return None

    config = SimpleNamespace(option=SimpleNamespace(markexpr=markexpr))
    items = [_Item(True), _Item(False)]

    assert conftest._should_fail_for_missing_env_services(config, items) is False
