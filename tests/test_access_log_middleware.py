from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from django.db import connection
from django.http import HttpResponse
from django.template import Template
from django.template.response import TemplateResponse
from django.test import RequestFactory
from django.utils.functional import SimpleLazyObject

from core.middleware import access_log
from core.middleware.access_log import AccessLogMiddleware


def test_access_log_sanitizes_control_characters(caplog):
    request = RequestFactory().get("/safe")
    request.path = "/foo\nbar"
    request.id = "req\r\n123"

    middleware = AccessLogMiddleware(lambda _req: HttpResponse("ok"))
    with caplog.at_level(logging.INFO, logger="access"):
        response = middleware(request)

    assert response.status_code == 200
    record = caplog.records[-1]
    message = record.getMessage()
    assert "path=/foo bar" in message
    assert "request_id=req  123" in message
    assert "status=200" in message


def test_access_log_includes_exception_name_when_response_raises(caplog):
    request = RequestFactory().get("/oops")

    def _raise(_req):
        raise ValueError("boom")

    middleware = AccessLogMiddleware(_raise)
    with pytest.raises(ValueError):
        with caplog.at_level(logging.INFO, logger="access"):
            middleware(request)

    message = caplog.records[-1].getMessage()
    assert "status=500" in message
    assert "exc=ValueError" in message


def _timing_clocks(monkeypatch, *, duration=1.2, executions=()):
    request_ticks = iter((0.0, duration))
    execution_ticks = iter(executions)
    monkeypatch.setattr(
        access_log,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(request_ticks),
            perf_counter=lambda: next(execution_ticks),
        ),
    )


@pytest.mark.django_db
def test_slow_request_records_execution_totals_without_sql_or_parameters(monkeypatch, caplog):
    _timing_clocks(monkeypatch, executions=(0.0, 0.2, 1.0, 1.4))
    wrappers_before = list(connection.execute_wrappers)

    def _response(_request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s", ["private-first-value"])
            assert cursor.fetchone() == ("private-first-value",)
            cursor.execute("SELECT %s", ["private-second-value"])
        return HttpResponse("ok")

    with caplog.at_level(logging.INFO, logger="access"):
        response = AccessLogMiddleware(_response)(RequestFactory().get("/manor/tasks/use-card/"))

    assert response.status_code == 200
    message = caplog.records[-1].getMessage()
    assert "db_execute_count=2 db_execute_ms=600.0 db_execute_max_ms=400.0" in message
    assert "SELECT" not in message
    assert "private-" not in message
    assert connection.execute_wrappers == wrappers_before


@pytest.mark.django_db
def test_template_render_queries_are_included(monkeypatch, caplog):
    _timing_clocks(monkeypatch, executions=(0.0, 0.3))

    def _template_value():
        with connection.cursor() as cursor:
            cursor.execute("SELECT 7")
            return cursor.fetchone()[0]

    # Django 的同步处理器在返回到中间件之前渲染 TemplateResponse。
    class _Template:
        def render(self, context, request):
            from django.template import Context

            return Template("{{ value }}").render(Context(context))

    def _response(request):
        return TemplateResponse(request, _Template(), {"value": SimpleLazyObject(_template_value)}).render()

    with caplog.at_level(logging.INFO, logger="access"):
        response = AccessLogMiddleware(_response)(RequestFactory().get("/template"))

    assert response.content == b"7"
    assert "db_execute_count=1 db_execute_ms=300.0 db_execute_max_ms=300.0" in caplog.records[-1].getMessage()


@pytest.mark.django_db
def test_failed_execution_is_counted_and_wrapper_is_removed(monkeypatch, caplog):
    from django.db import OperationalError

    _timing_clocks(monkeypatch, executions=(0.0, 0.4))
    wrappers_before = list(connection.execute_wrappers)

    def _response(_request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM missing_access_log_timing_table")

    with caplog.at_level(logging.INFO, logger="access"), pytest.raises(OperationalError):
        AccessLogMiddleware(_response)(RequestFactory().get("/broken"))

    message = caplog.records[-1].getMessage()
    assert "exc=OperationalError" in message
    assert "db_execute_count=1 db_execute_ms=400.0 db_execute_max_ms=400.0" in message
    assert "missing_access_log_timing_table" not in message
    assert connection.execute_wrappers == wrappers_before


def test_fast_request_keeps_original_log_fields(monkeypatch, caplog):
    _timing_clocks(monkeypatch, duration=0.1)
    with caplog.at_level(logging.INFO, logger="access"):
        AccessLogMiddleware(lambda _: HttpResponse("ok"))(RequestFactory().get("/fast"))

    message = caplog.records[-1].getMessage()
    assert "duration_ms=100" in message
    assert "db_execute_" not in message


def test_slow_request_without_queries_reports_zero_execution_time(monkeypatch, caplog):
    _timing_clocks(monkeypatch)
    with caplog.at_level(logging.INFO, logger="access"):
        AccessLogMiddleware(lambda _: HttpResponse("ok"))(RequestFactory().get("/wait"))

    assert "db_execute_count=0 db_execute_ms=0.0 db_execute_max_ms=0.0" in caplog.records[-1].getMessage()


def test_cancelled_request_preserves_exception_and_removes_wrappers(monkeypatch, caplog):
    from asyncio import CancelledError

    _timing_clocks(monkeypatch)
    wrappers_before = list(connection.execute_wrappers)

    def _cancel(_request):
        raise CancelledError()

    with caplog.at_level(logging.INFO, logger="access"), pytest.raises(CancelledError):
        AccessLogMiddleware(_cancel)(RequestFactory().get("/cancelled"))

    assert connection.execute_wrappers == wrappers_before
    assert "exc=CancelledError" in caplog.records[-1].getMessage()


@pytest.mark.parametrize("disabled,path", [(True, "/normal"), (False, "/static/file.css"), (False, "/media/file.png")])
def test_skipped_requests_do_not_install_execution_wrappers(settings, monkeypatch, disabled, path):
    settings.ACCESS_LOG_ENABLED = not disabled
    settings.STATIC_URL = "/static/"
    settings.MEDIA_URL = "/media/"

    def _unexpected_connections():
        raise AssertionError("Skipped request must not install query observers")

    monkeypatch.setattr(access_log.connections, "all", _unexpected_connections)
    assert AccessLogMiddleware(lambda _: HttpResponse("ok"))(RequestFactory().get(path)).status_code == 200
