"""Logging formatters with an explicit application display timezone."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import struct_time, time
from zoneinfo import ZoneInfo

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class ShanghaiFormatter(logging.Formatter):
    """Format ``asctime`` in the application's player-facing timezone."""

    @staticmethod
    def converter(timestamp: float | None) -> struct_time:
        if timestamp is None:
            timestamp = time()
        return datetime.fromtimestamp(timestamp, UTC).astimezone(SHANGHAI_TZ).timetuple()
