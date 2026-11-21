"""赛季玩法开放开关；关闭时冻结玩家入口与后台结算。"""

from __future__ import annotations

from django.conf import settings


def season_gameplay_enabled() -> bool:
    return bool(getattr(settings, "SEASON_GAMEPLAY_ENABLED", False))
