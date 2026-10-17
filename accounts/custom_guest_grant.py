"""在登录或进入首页时补发账号的一次性专属门客卡。"""

from __future__ import annotations

import logging

from core.exceptions import GameError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS, combine_infrastructure_exceptions

logger = logging.getLogger(__name__)
CARD_GRANT_RETRY_EXCEPTIONS = combine_infrastructure_exceptions(
    GameError, infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS
)


def grant_custom_guest_card_on_entry(user, *, manor=None) -> None:
    if not getattr(user, "is_authenticated", False):
        return
    from gameplay.models import Manor
    from guests.services.custom_guest import ensure_custom_guest_card

    try:
        if manor is None:
            manor = Manor.objects.filter(user_id=user.pk).first()
        if manor is not None:
            ensure_custom_guest_card(manor)
    except CARD_GRANT_RETRY_EXCEPTIONS:
        # 入库失败不阻断登录；领取标记与入库在同一事务，下次进入重试。
        logger.warning("Failed to grant custom guest card: user_id=%s", user.pk, exc_info=True)
