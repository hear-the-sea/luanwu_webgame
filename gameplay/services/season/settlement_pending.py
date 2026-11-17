"""结算前检查仍可能补记历史贡献的队伍。"""

from datetime import datetime

from django.db.models import Q

from gameplay.models import SeasonExpedition


def has_pending_contributions_locked(season, before: datetime) -> bool:
    """队伍最终贡献可能追溯至发车之后；归档前等待其入账。"""
    return SeasonExpedition.objects.filter(
        Q(kind="garrison", status="stationed")
        | Q(kind="pve", status__in=("outbound", "stationed", "failed"), result__has_key="collection_waves"),
        member__season=season,
        launched_at__lt=before,
    ).exists()
