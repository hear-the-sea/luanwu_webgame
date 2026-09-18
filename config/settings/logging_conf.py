"""
Logging configuration.
"""

from __future__ import annotations

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "core.middleware.logging_filters.RequestIDFilter",
        },
        "exclude_access": {
            "()": "core.middleware.logging_filters.ExcludeAccessLogFilter",
        },
    },
    "formatters": {
        "verbose": {
            "()": "core.logging_formatters.ShanghaiFormatter",
            "format": "%(levelname)s %(asctime)s %(name)s:%(lineno)s [request_id=%(request_id)s] %(message)s",
        },
        "access": {
            "()": "core.logging_formatters.ShanghaiFormatter",
            "format": "%(levelname)s %(asctime)s %(name)s:%(lineno)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id", "exclude_access"],
        },
        "access_console": {
            "class": "logging.StreamHandler",
            "formatter": "access",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "access": {
            "handlers": ["access_console"],
            "level": "INFO",
            "propagate": True,
        }
    },
}
