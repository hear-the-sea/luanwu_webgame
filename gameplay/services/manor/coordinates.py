"""庄园坐标约束辅助函数。"""

from __future__ import annotations

import re
from collections.abc import Iterator

from django.db import IntegrityError

_OCCUPIED_LOCATION_CONSTRAINT = "unique_occupied_manor_location"
_OCCUPIED_LOCATION_CONSTRAINT_PATTERN = re.compile(rf"(?<![0-9a-z_]){_OCCUPIED_LOCATION_CONSTRAINT}(?![0-9a-z_])")
_SQLITE_OCCUPIED_LOCATION_CONFLICT = (
    "unique constraint failed: gameplay_manor.occupied_region, "
    "gameplay_manor.coordinate_x, gameplay_manor.coordinate_y"
)


def is_occupied_manor_location_conflict(error: IntegrityError) -> bool:
    """判断异常是否由庄园已占用坐标唯一约束触发。"""
    for exception in _iter_exception_chain(error):
        message = str(exception).strip().lower()
        if _OCCUPIED_LOCATION_CONSTRAINT_PATTERN.search(message):
            return True
        if message == _SQLITE_OCCUPIED_LOCATION_CONFLICT:
            return True
    return False


def _iter_exception_chain(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        exception = pending.pop()
        if id(exception) in seen:
            continue
        seen.add(id(exception))
        yield exception
        if exception.__context__ is not None:
            pending.append(exception.__context__)
        if exception.__cause__ is not None:
            pending.append(exception.__cause__)
