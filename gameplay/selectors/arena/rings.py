from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.game_data.technology import troop_display_sort_key
from gameplay.models import ArenaRing, ArenaRingDaily, ArenaRingInspection, ArenaRingTenure, Manor
from gameplay.services.arena.ring_rules import ring_challenge_state
from gameplay.services.arena.ring_snapshots import ring_battle_troops
from guests.models import GuestStatus

from .common import build_common_context

RING_PRESENTATION = {
    "qinglong": {
        "image": "images/buildings/青龙擂.webp",
        "thumbnail": "images/buildings/青龙擂-thumb.webp",
        "description": "青旗招展，等你登台。",
    },
    "baihu": {
        "image": "images/buildings/白虎擂.webp",
        "thumbnail": "images/buildings/白虎擂-thumb.webp",
        "description": "虎踞石台，谁来争锋。",
    },
}


def visible_tenures(now: Any = None) -> Any:
    now = now or timezone.now()
    return ArenaRingTenure.objects.filter(
        Q(ended_at__isnull=True) | Q(ended_at__gt=now - timedelta(hours=24))
    ).select_related("ring", "manor")


def get_ring_event_rows(manor: Manor, params: Any = None) -> dict[str, Any]:
    from django.http import QueryDict

    now = timezone.now()
    query = params.copy() if isinstance(params, QueryDict) else QueryDict(mutable=True)
    groups = []
    for ring in (
        ArenaRing.objects.filter(key__in=RING_PRESENTATION).select_related("current_tenure__manor").order_by("id")
    ):
        parameter = f"{ring.key}_page"
        history = visible_tenures(now).filter(ring=ring).order_by("-started_at", "-id")
        page = Paginator(history, 10).get_page(query.get(parameter, 1))
        records = []
        for tenure in page:
            active = tenure.ended_at is None and tenure.ends_at > now
            until = min(now, tenure.ends_at, tenure.ended_at or now)
            records.append(
                {
                    "tenure": tenure,
                    "is_mine": tenure.manor_id == manor.pk,
                    "active": active,
                    "held_minutes": max(0, int((until - tenure.started_at).total_seconds() // 60)),
                    "status": "守擂中" if active else ("被夺擂" if tenure.end_reason == "defeated" else "已下台"),
                    "until": tenure.ended_at or (None if active else tenure.ends_at),
                    "detail_url": reverse("gameplay:arena_ring_event_detail", args=[tenure.pk]),
                }
            )

        def page_url(number):
            updated = query.copy()
            updated[parameter] = number
            return "?" + updated.urlencode() + f"#ring-history-{ring.key}"

        current = ring.current_tenure
        holding = current is not None and current.ended_at is None and current.ends_at > now
        groups.append(
            {
                "ring": ring,
                "image": RING_PRESENTATION[ring.key]["image"],
                "thumbnail": RING_PRESENTATION[ring.key]["thumbnail"],
                "current_holder": current.manor.display_name if current is not None and holding else "",
                "records": records,
                "page": page,
                "record_count": page.paginator.count,
                "expanded": parameter in query,
                "previous_url": page_url(page.previous_page_number()) if page.has_previous() else "",
                "next_url": page_url(page.next_page_number()) if page.has_next() else "",
            }
        )
    return {"ring_history_groups": groups}


def lineup_display(lineup: dict[str, Any]) -> dict[str, Any]:
    from battle.troops import load_troop_templates
    from guests.models import Skill

    definitions = load_troop_templates()
    guests = [dict(guest) for guest in lineup.get("guests", [])]
    skill_keys = {key for guest in guests for key in guest.get("skill_keys", [])}
    skills = dict(Skill.objects.filter(key__in=skill_keys).values_list("key", "name"))
    for guest in guests:
        guest["skill_names"] = [skills.get(key, "未知技能") for key in guest.get("skill_keys", [])]
    return {
        "guests": guests,
        "troops": [
            {"name": definitions.get(key, {}).get("label", key), "count": count}
            for key, count in sorted(
                ring_battle_troops(lineup).items(), key=lambda item: troop_display_sort_key(item[0])
            )
            if count > 0
        ],
    }


def get_arena_rings_context(manor: Manor) -> dict[str, Any]:
    from gameplay.services.arena.ring import load_ring_rules

    now = timezone.now()
    context = build_common_context(manor)
    rules = load_ring_rules()
    context["ring_rules"] = rules
    context["ring_tenure_hours"] = rules["tenure_seconds"] // 3600
    context["ring_return_minutes"] = rules["cooldown_seconds"] // 60
    my_tenure = ArenaRingTenure.objects.filter(manor=manor, ended_at__isnull=True).first()
    guests = list(
        manor.guests.exclude(status=GuestStatus.INJURED)
        .select_related("template")
        .prefetch_related("gear_items__template", "skills")
        .order_by("-level", "id")
    )
    selected_ids = (
        {g["guest_id"] for g in my_tenure.lineup["guests"][: rules["max_guests"]]}
        if my_tenure
        else {g.id for g in guests[: rules["max_guests"]]}
    )
    guest_rows: list[dict[str, Any]] = [
        {"guest": guest, "selected": guest.id in selected_ids, "capacity": guest.troop_capacity} for guest in guests
    ]
    from battle.troops import load_troop_templates

    stock: dict[str, dict[str, Any]] = {
        key: {"key": key, "name": definition["label"], "available": 0, "selected": 0, "manor_count": 0, "bank_count": 0}
        for key, definition in load_troop_templates().items()
    }
    for source, troops in (
        ("manor_count", manor.troops.select_related("troop_template")),
        ("bank_count", manor.troop_bank_storages.select_related("troop_template")),
    ):
        for troop in troops:
            key = troop.troop_template.key
            row = stock.setdefault(
                key,
                {
                    "key": key,
                    "name": troop.troop_template.name,
                    "available": 0,
                    "selected": 0,
                    "manor_count": 0,
                    "bank_count": 0,
                },
            )
            row[source] += troop.count
            row["available"] += troop.count
    if my_tenure:
        for key, count in ring_battle_troops(my_tenure.lineup).items():
            if key in stock:
                stock[key]["selected"] = count
    ring_rows = []
    for ring in (
        ArenaRing.objects.filter(key__in=RING_PRESENTATION).select_related("current_tenure__manor").order_by("id")
    ):
        tenure = ring.current_tenure
        mine = tenure is not None and tenure.manor_id == manor.id
        challenge_state = ring_challenge_state(tenure, manor, now) if tenure and not mine else {}
        ring_rows.append(
            {
                "ring": ring,
                **RING_PRESENTATION[ring.key],
                "tenure": tenure,
                "is_mine": mine,
                **challenge_state,
                "can_enter_action": mine or not tenure or challenge_state["can_challenge"],
                "can_enter": my_tenure is None or mine,
                "action_kind": "update" if mine else ("challenge" if tenure else "occupy"),
                "action_label": "调整阵容" if mine else ("挑战擂主" if tenure else "登台守擂"),
                "action_token": str(uuid4()),
                "inspect_token": str(uuid4()),
                "leave_token": str(uuid4()),
                "held_minutes": (
                    int((min(now, tenure.ends_at) - tenure.started_at).total_seconds() // 60) if tenure else 0
                ),
                "own_lineup": lineup_display(tenure.lineup) if mine and tenure is not None else None,
            }
        )
    context.update(
        {
            "ring_rows": ring_rows,
            "ring_guest_rows": guest_rows,
            "ring_total_capacity": sum(row["capacity"] for row in guest_rows if row["selected"]),
            "ring_troop_rows": sorted(stock.values(), key=lambda row: troop_display_sort_key(row["key"])),
            "ring_daily": ArenaRingDaily.objects.filter(manor=manor, date=timezone.localdate(now)).first(),
            "my_ring_tenure": my_tenure,
        }
    )
    return context


def get_ring_event_detail_context(manor: Manor, tenure_id: int, page: Any = 1) -> dict[str, Any] | None:
    tenure = visible_tenures().filter(pk=tenure_id).first()
    if tenure is None:
        return None
    context = build_common_context(manor)
    matches = tenure.matches.select_related("attacker", "defender", "winner", "battle_report").order_by("-created_at")
    match_page = Paginator(matches, 30).get_page(page)
    context.update(
        {
            "ring_tenure": tenure,
            "ring_event_matches": match_page,
            "event_page": match_page,
            "ring_image": RING_PRESENTATION[tenure.ring.key]["image"],
            "ring_is_mine": tenure.manor_id == manor.id,
            "ring_own_lineup": lineup_display(tenure.lineup) if tenure.manor_id == manor.id else None,
        }
    )
    return context


def get_ring_inspection_context(manor: Manor, inspection_id: int) -> dict[str, Any] | None:
    inspection = (
        ArenaRingInspection.objects.select_related("tenure__ring", "tenure__manor")
        .filter(pk=inspection_id, buyer=manor)
        .first()
    )
    if inspection is None or not visible_tenures().filter(pk=inspection.tenure_id).exists():
        return None
    context = build_common_context(manor)
    context.update({"ring_inspection": inspection, "ring_inspected_lineup": lineup_display(inspection.lineup)})
    return context
