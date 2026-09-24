"""拍卖行声望准入规则。"""

from core.config import TRADE
from core.exceptions import TradeValidationError
from core.utils import safe_int
from gameplay.models import Manor

AUCTION_MIN_PRESTIGE = TRADE.AUCTION_MIN_PRESTIGE


def get_auction_prestige(manor: Manor) -> int:
    return safe_int(getattr(manor, "prestige", 0), 0, min_val=0)


def can_enter_auction(manor: Manor) -> bool:
    return get_auction_prestige(manor) >= AUCTION_MIN_PRESTIGE


def require_auction_access(manor: Manor) -> None:
    current_prestige = safe_int(
        Manor.objects.filter(pk=manor.pk).values_list("prestige", flat=True).first(),
        0,
        min_val=0,
    )
    if current_prestige < AUCTION_MIN_PRESTIGE:
        raise TradeValidationError(f"进入拍卖行需要声望达到 {AUCTION_MIN_PRESTIGE}，当前声望为 {current_prestige}")
