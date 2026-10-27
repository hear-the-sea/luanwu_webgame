"""从长期保留的赛季流水识别有效阵营对抗，不依赖短期战报。"""

from typing import Any

from django.db.models import Count, F, Q

from gameplay.models import SeasonAction


def effective_contested_actions(season: Any):
    """真实战报、稳定标记及旧战报链接均可证明交战；拆城和同阵营不计。"""
    evidence = (
        Q(report__isnull=False)
        | Q(result__pvp_battle=True)
        | (Q(request_hash="settled-expedition", result__has_key="report_url") & ~Q(result__report_url=""))
    )
    return SeasonAction.objects.filter(
        evidence,
        Q(result__requested_merit__gt=0) | Q(result__merit__gt=0),
        member__season=season,
        member__faction__in=("fiction", "history"),
        opponent__season=season,
        opponent__faction__in=("fiction", "history"),
        kind="combat",
    ).exclude(member__faction=F("opponent__faction"))


def victory_contested_days(season: Any) -> dict[str, int]:
    days = {"fiction": 0, "history": 0}
    rows = effective_contested_actions(season).values("member__faction").annotate(days=Count("date", distinct=True))
    days.update({row["member__faction"]: row["days"] for row in rows})
    return days
