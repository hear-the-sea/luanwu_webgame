from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaExchangeRecord, ArenaTournament, Manor
from guests.models import GuestStatus

from .common import build_common_context, build_reward_rows, build_summary_metrics, today_coop_participation_stats


def get_active_entry(manor: Manor) -> ArenaEntry | None:
    return (
        ArenaEntry.objects.select_related("tournament")
        .prefetch_related("entry_guests__guest")
        .filter(
            manor=manor,
            tournament__status__in=[
                ArenaTournament.Status.RECRUITING,
                ArenaTournament.Status.RUNNING,
            ],
        )
        .order_by("-joined_at")
        .first()
    )


def get_active_coop_entry(manor: Manor):
    return (
        ArenaCoopEntry.objects.select_related("event")
        .prefetch_related("entry_guests__guest")
        .filter(
            manor=manor,
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        )
        .order_by("-joined_at", "-id")
        .first()
    )


def get_arena_coop_summary_context(manor: Manor) -> dict:
    coop_today_participations, coop_remaining_daily = today_coop_participation_stats(manor)
    active_entry = get_active_coop_entry(manor)
    available_guests = manor.guests.none()
    selected_guest_ids: set[int] = set()

    if active_entry:
        selected_guest_ids = set(active_entry.entry_guests.values_list("guest_id", flat=True))
    elif coop_remaining_daily > 0:
        available_guests = (
            manor.guests.select_related("template").filter(status=GuestStatus.IDLE).order_by("-level", "id")
        )

    recruiting_event = (
        ArenaCoopEvent.objects.filter(status=ArenaCoopEvent.Status.RECRUITING)
        .annotate(
            registered_entry_count=Count(
                "entries",
                filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
            )
        )
        .first()
    )

    return {
        "arena_coop_today_participations": coop_today_participations,
        "arena_coop_remaining_daily": coop_remaining_daily,
        "arena_coop_daily_limit": arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
        "arena_coop_event": {
            "name": "围攻光明顶",
            "subtitle": "5 人共斗",
            "description": (
                "江湖告急！魔教教主不仅深夜在山头高唱跑调山歌严重扰民，还拒不缴纳今年的武林卫生管理费！各大门派彻底怒了，誓要荡平这破山头！ "
                "别装大侠讲究什么单挑了，这年头流行并肩子群殴！请立刻揪出你院子里最能打（或最能抗揍）的3名门客，出门左拐与其他路过的暴躁老哥临时结拜、强行组队！大家凑成一个乌合之众，啊不，正义联盟，浩浩荡荡杀向光明顶！ "
                "讲什么武道规矩，咱们主打一个仗势欺人！冲上去不为除魔卫道，就为了抢魔教食堂那口祖传的大铁锅！打赢了分行李，不对，分战利品，打输了，大不了就地躺下投降，反正魔教包吃包住！抄家伙，上啊！"
            ),
            "player_limit": arena_coop_core.ARENA_COOP_PLAYER_LIMIT,
            "guest_limit_per_entry": arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY,
            "daily_limit": arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
            "registration_hint": "武林高手齐聚光明顶，请派遣3名主力门客参战",
            "summary_metrics": build_summary_metrics(
                ("报名人数", f"{arena_coop_core.ARENA_COOP_PLAYER_LIMIT} 人满员开战"),
                (
                    "上阵人数",
                    f"每人 {arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY} 名主力",
                ),
                (
                    "每日次数",
                    f"{arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT} 次",
                ),
            ),
        },
        "arena_coop_active_entry": active_entry,
        "arena_coop_recruiting_event": recruiting_event,
        "arena_coop_virtual_fill_due": bool(
            recruiting_event
            and recruiting_event.virtual_fill_at
            and recruiting_event.virtual_fill_at <= timezone.now()
            and not recruiting_event.virtual_fill_completed
        ),
        "arena_coop_available_guests": available_guests,
        "arena_coop_selected_guest_ids": selected_guest_ids,
    }


def get_arena_registration_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context.update(get_arena_coop_summary_context(manor))
    active_entry = get_active_entry(manor)

    context["active_entry"] = active_entry
    recruiting_tournament = (
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
        .annotate(entry_count=Count("entries"))
        .order_by("created_at")
        .first()
    )
    context["recruiting_tournament"] = recruiting_tournament
    context["recruiting_tournament_virtual_fill_due"] = bool(
        recruiting_tournament
        and recruiting_tournament.virtual_fill_at
        and recruiting_tournament.virtual_fill_at <= timezone.now()
        and not recruiting_tournament.virtual_fill_completed
    )
    selected_guest_ids: set[int] = set()
    available_guests = manor.guests.none()

    if active_entry:
        selected_guest_ids = set(active_entry.entry_guests.values_list("guest_id", flat=True))
    elif context["remaining_daily"] > 0:
        available_guests = (
            manor.guests.select_related("template").filter(status=GuestStatus.IDLE).order_by("-level", "id")
        )

    context["available_guests"] = available_guests
    context["selected_guest_ids"] = selected_guest_ids
    return context


def get_arena_exchange_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context["reward_rows"] = build_reward_rows(manor)
    context["recent_exchange_records"] = ArenaExchangeRecord.objects.filter(manor=manor).order_by("-created_at")[:15]
    return context
