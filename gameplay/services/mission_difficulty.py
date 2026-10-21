"""读取离线正式战斗模拟生成的个人任务难度评级。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings


def load_mission_difficulty_ratings() -> dict[str, dict[str, Any]]:
    path = settings.BASE_DIR / "data" / "mission_difficulty_ratings.json"
    return _load_ratings(path, path.stat().st_mtime_ns)


@lru_cache(maxsize=1)
def _load_ratings(path: Path, modified_at: int) -> dict[str, dict[str, Any]]:
    # 用文件版本作为缓存键，使重测后的评级能在下一次请求生效。
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("ratings"), dict):
        raise AssertionError("invalid mission difficulty ratings")
    return payload["ratings"]
