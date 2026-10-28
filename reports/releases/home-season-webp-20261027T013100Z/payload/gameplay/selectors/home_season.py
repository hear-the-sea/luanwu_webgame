"""首页赛季队伍摘要；只读取当前玩家的未归队记录。"""

from __future__ import annotations

from uuid import uuid4

from django.urls import reverse
from django.utils import timezone

from gameplay.models import SeasonExpedition
from gameplay.services.season.warfare import ACTIVE_STATUSES, campaign_nodes


def get_home_season_events(manor, *, now=None) -> list[dict]:
    now = now or timezone.now()
    runs = (
        SeasonExpedition.objects.filter(member__manor_id=manor.pk, status__in=ACTIVE_STATUSES)
        .select_related("member__season", "rally")
        .order_by("due_at", "id")
    )
    nodes_by_season = {}
    events = []
    for run in runs:
        season = run.member.season
        if season.pk not in nodes_by_season:
            nodes_by_season[season.pk] = campaign_nodes(season)
        title = "赛季出征"
        icon = "sword"
        if run.rally_id:
            title, icon = "赛季集结", "flag"
        elif run.kind == "garrison":
            title, icon = "赛季驻军", "shield"
        elif run.kind == "escort":
            title, icon = "赛季护送", "shield"
        elif run.kind == "transport":
            title, icon = "赛季运输", "cart"

        status_label = run.get_status_display()
        deadline = run.due_at
        deadline_label = "下一阶段"
        if run.status == "gathering":
            deadline = run.rally.gathers_until if run.rally_id else run.due_at
            deadline_label = "集结截止"
        elif run.status == "outbound":
            deadline_label = "预计抵达"
            if run.rally_id and run.rally.result.get("wave", 0):
                status_label, deadline_label = "连续交战中", "下轮交战"
        elif run.status == "stationed":
            deadline = run.stationed_until or run.due_at
            deadline_label = "驻守结束"
        elif run.status == "transporting":
            if run.kind in {"intercept", "escort"}:
                status_label, deadline_label = "等待遭遇战", "遭遇战结算"
            else:
                deadline_label = "运输结算"
        elif run.status == "returning":
            deadline_label = "预计归队"
        elif run.status == "failed":
            deadline = None
            status_label = "等待处理"

        events.append(
            {
                "id": run.pk,
                "season_id": season.pk,
                "action_token": str(uuid4()),
                "can_recall": run.status in {"gathering", "outbound", "stationed", "transporting", "failed"},
                "recall_label": (
                    ("取消集结" if run.rally_id and run.rally.leader_id == run.member_id else "退出集结")
                    if run.status == "gathering"
                    else "撤退"
                ),
                "title": title,
                "icon": icon,
                "node_name": nodes_by_season[season.pk].get(run.node_key, {}).get("name", run.node_key),
                "status_label": status_label,
                "guest_names": "、".join(row.get("display_name", "门客") for row in run.lineup.get("guests", [])),
                "troop_count": sum(run.troops.values()),
                "deadline": deadline,
                "deadline_label": deadline_label,
                "awaiting_settlement": bool(deadline and deadline <= now),
                "details_url": f"{reverse('gameplay:season')}?season_id={season.pk}#season-team-status",
            }
        )
    return events
