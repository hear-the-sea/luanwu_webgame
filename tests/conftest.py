"""
Pytest 配置和共享 fixtures
"""

import ast
import os
from pathlib import Path
from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.test import Client

from battle.models import TroopTemplate
from gameplay.models import PlayerTroop
from gameplay.services.manor.core import ensure_manor
from tests.jail_persuasion.conftest import persuasion_world as persuasion_world  # noqa: F401

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent


def _run_project_command(command: str, /, **kwargs) -> None:
    original_cwd = os.getcwd()
    os.chdir(PROJECT_ROOT)
    try:
        call_command(command, **kwargs)
    finally:
        os.chdir(original_cwd)


def ensure_test_schema_ready() -> None:
    from django.db import connection

    if "django_migrations" not in connection.introspection.table_names():
        call_command("migrate", verbosity=0, interactive=False)


def ensure_troop_data_loaded() -> None:
    if TroopTemplate.objects.exists():
        return
    _run_project_command("load_troop_templates", verbosity=0, skip_images=True)


def ensure_guest_data_loaded() -> None:
    from guests.models import GuestTemplate, RecruitmentPool

    if GuestTemplate.objects.exists() and RecruitmentPool.objects.exists():
        return
    _run_project_command("load_guest_templates", verbosity=0, skip_images=True)


def ensure_game_data_loaded() -> None:
    ensure_test_schema_ready()
    ensure_troop_data_loaded()
    ensure_guest_data_loaded()


def _env_services_enabled() -> bool:
    return os.environ.get("DJANGO_TEST_USE_ENV_SERVICES", "0") == "1"


def _real_service_gate_hint() -> str:
    return "Run `DJANGO_TEST_USE_ENV_SERVICES=1 make test-integration` or `make test-real-services`."


def _gate_outcome(message: str, *, strict: bool) -> None:
    detailed_message = f"{message}. {_real_service_gate_hint()}"
    if strict:
        pytest.fail(detailed_message, pytrace=False)
    pytest.skip(detailed_message)


def _test_gate_mode() -> str:
    if _env_services_enabled():
        return "real-external-services"
    return getattr(settings, "TEST_GATE_MODE", "hermetic")


def _test_gate_description(mode: str) -> str:
    if mode == "real-external-services":
        return "Real external-service semantics (non-hermetic dependencies)."
    return getattr(
        settings,
        "TEST_GATE_DESCRIPTION",
        "Hermetic rapid gate (SQLite, LocMem cache, InMemoryChannelLayer, memory Celery).",
    )


def _markexpr_explicitly_selects_integration(markexpr: str) -> bool:
    """Return True when the marker expression positively selects integration tests.

    `pytest -m "not integration"` must stay on the hermetic gate and should not
    be treated as an explicit request for real external services.
    """
    expression = (markexpr or "").strip()
    if not expression:
        return False

    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError:
        return False

    def _has_positive_integration(node: ast.AST, *, positive: bool = True) -> bool:
        if isinstance(node, ast.Name):
            return positive and node.id == "integration"

        if isinstance(node, ast.Call):
            return _has_positive_integration(node.func, positive=positive)

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return _has_positive_integration(node.operand, positive=not positive)

        if isinstance(node, ast.BoolOp):
            return any(_has_positive_integration(value, positive=positive) for value in node.values)

        if isinstance(node, ast.Expression):
            return _has_positive_integration(node.body, positive=positive)

        return False

    return _has_positive_integration(parsed)


def _should_fail_for_missing_env_services(config, items) -> bool:
    if _env_services_enabled():
        return False

    integration_items = [item for item in items if item.get_closest_marker("integration") is not None]
    if not integration_items:
        return False

    markexpr = str(getattr(getattr(config, "option", None), "markexpr", "") or "").strip()
    if _markexpr_explicitly_selects_integration(markexpr):
        return True

    return len(integration_items) == len(items)


def _require_non_sqlite_database(settings, *, strict: bool) -> None:
    db_engine = str(settings.DATABASES["default"].get("ENGINE", ""))
    if "sqlite" in db_engine:
        _gate_outcome("integration tests require non-sqlite database backend", strict=strict)


def _require_external_cache_backend(settings, cache, *, strict: bool) -> None:
    cache_backend = str(settings.CACHES["default"].get("BACKEND", "")).lower()
    if "locmem" in cache_backend:
        _gate_outcome("integration tests require external cache backend", strict=strict)

    probe_key = "integration:services:probe"
    try:
        cache.set(probe_key, "1", timeout=5)
        if cache.get(probe_key) != "1":
            _gate_outcome("integration tests require a writable external cache backend", strict=strict)
        cache.delete(probe_key)
    except Exception as exc:
        _gate_outcome(f"integration tests require reachable external cache backend: {exc}", strict=strict)


def _require_external_channel_layer(settings, channel_layer, *, strict: bool) -> None:
    default_layer = settings.CHANNEL_LAYERS.get("default", {})
    backend = str(default_layer.get("BACKEND", "")).lower()
    if "inmemory" in backend:
        _gate_outcome("integration tests require external channel layer backend", strict=strict)
    if channel_layer is None:
        _gate_outcome("integration tests require configured channel layer", strict=strict)

    try:
        channel_name = async_to_sync(channel_layer.new_channel)("integration.probe.")
        payload = {"type": "integration.probe", "value": "ok"}
        async_to_sync(channel_layer.send)(channel_name, payload)
        received = async_to_sync(channel_layer.receive)(channel_name)
        if received != payload:
            _gate_outcome("integration tests require reliable external channel layer backend", strict=strict)
    except Exception as exc:
        _gate_outcome(f"integration tests require reachable external channel layer backend: {exc}", strict=strict)


def _require_external_celery_broker(celery_app, *, strict: bool) -> None:
    broker_url = str(getattr(celery_app.conf, "broker_url", "") or "")
    result_backend = str(getattr(celery_app.conf, "result_backend", "") or "")
    if not broker_url or broker_url.startswith("memory://"):
        _gate_outcome("integration tests require external Celery broker", strict=strict)
    if result_backend.startswith("cache+memory://"):
        _gate_outcome("integration tests require external Celery result backend", strict=strict)

    try:
        with celery_app.connection() as connection:
            connection.ensure_connection(max_retries=1)
    except Exception as exc:
        _gate_outcome(f"integration tests require reachable Celery broker: {exc}", strict=strict)


@pytest.fixture(scope="function")
def game_data(django_db_setup, django_db_blocker):
    """Load shared game templates used by integration-style tests.

    Notes:
    - Not autouse: avoids slowing down unit-like tests.
    - Uses `skip_images=True` to keep tests fast and hermetic.
    """
    with django_db_blocker.unblock():
        ensure_game_data_loaded()


@pytest.fixture(scope="function")
def mission_templates(django_db_setup, django_db_blocker):
    """
    在每个测试中加载任务模板（需要事务支持）
    """
    with django_db_blocker.unblock():
        _run_project_command("load_mission_templates", file="data/mission_templates.yaml", verbosity=0)


@pytest.fixture(scope="function")
def load_guest_data(django_db_setup, django_db_blocker):
    """Ensure guest templates and recruitment pools exist for transactional tests."""
    with django_db_blocker.unblock():
        ensure_guest_data_loaded()


@pytest.fixture(scope="function")
def load_troop_data(django_db_setup, django_db_blocker):
    """Ensure troop templates exist for transactional tests."""
    with django_db_blocker.unblock():
        ensure_troop_data_loaded()


@pytest.fixture(scope="function")
def manor_with_troops(django_user_model, django_db_blocker):
    """
    创建拥有基础护院的庄园和用户
    用于测试需要护院的场景（如出征）
    """
    from gameplay.services.manor.core import ensure_manor
    from guests.models import GuestTemplate

    user = django_user_model.objects.create_user(username="troop_player", password="pass123")
    manor = ensure_manor(user)

    # 需要解除数据库阻塞才能访问数据库
    with django_db_blocker.unblock():
        if not TroopTemplate.objects.exists():
            call_command("load_troop_templates", verbosity=0, skip_images=True)

        # 在事务外创建护院（使用 transaction.atomic 确保提交）
        common_troop_types = ["archer", "dao_jie", "qiang_ling", "jian_shi", "fist_master"]

        with transaction.atomic():
            for troop_key in common_troop_types:
                troop_template = TroopTemplate.objects.filter(key=troop_key).first()
                if troop_template:
                    PlayerTroop.objects.get_or_create(
                        manor=manor, troop_template=troop_template, defaults={"count": 1000}
                    )

        # 确保至少有一个门客模板（用于测试）
        if not GuestTemplate.objects.exists():
            call_command("load_guest_templates", verbosity=0, skip_images=True)

    return manor


@pytest.fixture
def authenticated_client(django_user_model):
    """返回已登录的测试客户端。"""
    user = django_user_model.objects.create_user(username="testplayer", password="testpass123")
    client = Client()
    client.login(username="testplayer", password="testpass123")
    client.user = user
    return client


@pytest.fixture
def manor_with_user(authenticated_client):
    """返回带庄园的用户。"""
    manor = ensure_manor(authenticated_client.user)
    return manor, authenticated_client


@pytest.fixture
def user_factory(django_user_model):
    def _create_user(**overrides):
        username = overrides.pop("username", f"test_user_{uuid4().hex[:8]}")
        password = overrides.pop("password", "testpass123")
        return django_user_model.objects.create_user(username=username, password=password, **overrides)

    return _create_user


@pytest.fixture
def auth_client_factory(user_factory):
    def _create_client(**user_overrides):
        user = user_factory(**user_overrides)
        client = Client()
        assert client.login(username=user.username, password=user_overrides.get("password", "testpass123"))
        client.user = user
        return client, user

    return _create_client


@pytest.fixture
def manor_factory(user_factory):
    def _create_manor(**user_overrides):
        user = user_factory(**user_overrides)
        manor = ensure_manor(user)
        return manor, user

    return _create_manor


@pytest.fixture(scope="session")
def require_env_services():
    """Validate integration external services; strict-fail when explicit real-service gate is enabled."""
    if not _env_services_enabled():
        pytest.skip(f"integration tests require DJANGO_TEST_USE_ENV_SERVICES=1. {_real_service_gate_hint()}")

    from django.conf import settings
    from django.core.cache import cache

    from config.celery import app as celery_app

    _require_non_sqlite_database(settings, strict=True)
    _require_external_cache_backend(settings, cache, strict=True)
    _require_external_channel_layer(settings, get_channel_layer(), strict=True)
    _require_external_celery_broker(celery_app, strict=True)


def pytest_collection_modifyitems(config, items):
    """Ensure all integration tests run behind the same external-service gate."""
    if _should_fail_for_missing_env_services(config, items):
        raise pytest.UsageError(
            f"integration tests require DJANGO_TEST_USE_ENV_SERVICES=1. {_real_service_gate_hint()}"
        )

    for item in items:
        if item.get_closest_marker("integration") is not None:
            item.add_marker(pytest.mark.usefixtures("require_env_services"))


def pytest_report_header(config):
    gate_mode = _test_gate_mode()
    if gate_mode == "real-external-services":
        return (
            "Test gate: real external-service semantics (DJANGO_TEST_USE_ENV_SERVICES=1). "
            "Use 'make test-real-services' to run this gate."
        )

    description = _test_gate_description(gate_mode)
    return f"Test gate: {description}. Run 'make test-real-services' for real external services."
