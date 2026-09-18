from __future__ import annotations

import logging
from datetime import UTC, datetime

from config.settings.logging_conf import LOGGING
from core.logging_formatters import ShanghaiFormatter


def test_console_handler_uses_request_id_filter():
    console_handler = LOGGING["handlers"]["console"]

    assert "request_id" in LOGGING["filters"]
    assert "exclude_access" in LOGGING["filters"]
    assert console_handler["filters"] == ["request_id", "exclude_access"]
    assert "%(request_id)s" in LOGGING["formatters"]["verbose"]["format"]


def test_access_logger_uses_dedicated_handler_without_request_id_formatter():
    access_logger = LOGGING["loggers"]["access"]
    access_handler = LOGGING["handlers"]["access_console"]

    assert access_logger["handlers"] == ["access_console"]
    assert access_logger["propagate"] is True
    assert access_handler["formatter"] == "access"
    assert "%(request_id)s" not in LOGGING["formatters"]["access"]["format"]


def test_application_log_formatters_use_explicit_shanghai_timezone():
    assert LOGGING["formatters"]["verbose"]["()"] == "core.logging_formatters.ShanghaiFormatter"
    assert LOGGING["formatters"]["access"]["()"] == "core.logging_formatters.ShanghaiFormatter"


def test_shanghai_formatter_converts_utc_asctime():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="test",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 9, 18, 1, 2, 3, tzinfo=UTC).timestamp()
    record.msecs = 0

    assert ShanghaiFormatter("%(asctime)s").format(record) == "2026-09-18 09:02:03,000"
