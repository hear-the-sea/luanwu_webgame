from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import DatabaseError

from gameplay.request_context import (
    PREPARED_MANOR_REQUEST_ATTR,
    clear_prepared_manor,
    get_prepared_manor,
    set_prepared_manor,
)
from gameplay.views.read_helpers import get_prepared_manor_for_read, prepare_manor_for_read


def test_prepare_manor_for_read_degrades_database_error():
    logger = MagicMock()
    failures: list[str] = []

    result = prepare_manor_for_read(
        SimpleNamespace(id=1),
        project_fn=lambda _manor: (_ for _ in ()).throw(DatabaseError("db down")),
        logger=logger,
        source="unit-test",
        on_expected_failure=lambda exc: failures.append(str(exc)),
    )

    assert result is False
    assert failures == ["db down"]
    logger.warning.assert_called_once()


def test_prepare_manor_for_read_does_not_swallow_runtime_keyword_guess():
    logger = MagicMock()

    with pytest.raises(RuntimeError, match="cache backend unavailable"):
        prepare_manor_for_read(
            SimpleNamespace(id=1),
            project_fn=lambda _manor: (_ for _ in ()).throw(RuntimeError("cache backend unavailable")),
            logger=logger,
            source="unit-test",
        )

    logger.warning.assert_not_called()


def test_get_prepared_manor_for_read_loads_manor_and_projects(monkeypatch):
    logger = MagicMock()
    request = SimpleNamespace(user=SimpleNamespace(id=99))
    manor = SimpleNamespace(id=7)
    calls: list[tuple[str, object]] = []

    def _fake_get_manor(user):
        calls.append(("get_manor", user))
        return manor

    def _fake_project(target_manor):
        calls.append(("project", target_manor))

    monkeypatch.setattr("gameplay.views.read_helpers.get_manor", _fake_get_manor)

    result = get_prepared_manor_for_read(
        request,
        project_fn=_fake_project,
        logger=logger,
        source="unit-test",
    )

    assert result is manor
    assert calls == [("get_manor", request.user), ("project", manor)]
    assert getattr(request, PREPARED_MANOR_REQUEST_ATTR) is manor
    logger.warning.assert_not_called()


def test_get_prepared_manor_for_read_does_not_cache_failed_projection(monkeypatch):
    logger = MagicMock()
    request = SimpleNamespace(user=SimpleNamespace(id=99))
    manor = SimpleNamespace(id=7)

    monkeypatch.setattr("gameplay.views.read_helpers.get_manor", lambda _user: manor)

    result = get_prepared_manor_for_read(
        request,
        project_fn=lambda _manor: (_ for _ in ()).throw(DatabaseError("db down")),
        logger=logger,
        source="unit-test",
    )

    assert result is manor
    assert not hasattr(request, PREPARED_MANOR_REQUEST_ATTR)


def test_get_prepared_manor_for_read_clears_previous_projection_after_failure(monkeypatch):
    logger = MagicMock()
    request = SimpleNamespace(user=SimpleNamespace(id=99))
    first_manor = SimpleNamespace(id=7)
    second_manor = SimpleNamespace(id=8)
    manors = iter((first_manor, second_manor))

    monkeypatch.setattr("gameplay.views.read_helpers.get_manor", lambda _user: next(manors))

    get_prepared_manor_for_read(
        request,
        project_fn=lambda _manor: None,
        logger=logger,
        source="unit-test",
    )
    assert getattr(request, PREPARED_MANOR_REQUEST_ATTR) is first_manor

    result = get_prepared_manor_for_read(
        request,
        project_fn=lambda _manor: (_ for _ in ()).throw(DatabaseError("db down")),
        logger=logger,
        source="unit-test",
    )

    assert result is second_manor
    assert not hasattr(request, PREPARED_MANOR_REQUEST_ATTR)


def test_get_prepared_manor_for_read_clears_previous_projection_before_loading(monkeypatch):
    logger = MagicMock()
    request = SimpleNamespace(user=SimpleNamespace(id=99))
    set_prepared_manor(request, SimpleNamespace(pk=7, user_id=99))
    monkeypatch.setattr(
        "gameplay.views.read_helpers.get_manor",
        MagicMock(side_effect=DatabaseError("db down")),
    )

    with pytest.raises(DatabaseError, match="db down"):
        get_prepared_manor_for_read(
            request,
            project_fn=lambda _manor: None,
            logger=logger,
            source="unit-test",
        )

    assert not hasattr(request, PREPARED_MANOR_REQUEST_ATTR)


def test_request_manor_context_is_owner_scoped_and_clearable():
    request = SimpleNamespace()
    manor = SimpleNamespace(pk=7, user_id=99)

    set_prepared_manor(request, manor)

    assert get_prepared_manor(request, user_id=99) is manor
    assert get_prepared_manor(request, user_id=100) is None

    clear_prepared_manor(request)

    assert get_prepared_manor(request, user_id=99) is None
