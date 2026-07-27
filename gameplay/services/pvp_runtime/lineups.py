from __future__ import annotations

from typing import TYPE_CHECKING

from guests.models import Guest, GuestStatus
from guests.query_utils import guest_template_rarity_rank_case

if TYPE_CHECKING:
    from gameplay.models import Manor


def select_player_defender_lineup(manor: Manor) -> list[Guest]:
    """Lock and return the strongest eligible PVP defenders within the manor limit."""
    limit = max(0, int(manor.max_squad_size or 0))
    if limit == 0:
        return []

    return list(
        manor.guests.select_for_update()
        .filter(status=GuestStatus.IDLE)
        .select_related("template")
        .prefetch_related("skills")
        .annotate(_template_rarity_rank=guest_template_rarity_rank_case("template__rarity"))
        .order_by("-_template_rarity_rank", "-level", "id")[:limit]
    )
