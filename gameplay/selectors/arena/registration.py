from __future__ import annotations

from django.db.models import Count, Q

import gameplay.services.arena.coop_core as arena_coop_core
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaExchangeRecord,
    ArenaTournament,
    ArenaWheelEntry,
    ArenaWheelEvent,
    Manor,
)
from gameplay.services.arena.currency_exchange import get_arena_currency_exchange_status
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
            "subtitle": "",
            "description": (
                "江湖告急！魔教教主不仅深夜在山头高唱跑调山歌严重扰民，还拒不缴纳今年的武林卫生管理费！各大门派彻底怒了，誓要荡平这破山头！ "
                "别装大侠讲究什么单挑了，这年头流行并肩子群殴！请立刻揪出你院子里最能打（或最能抗揍）的3名门客，出门左拐与其他路过的暴躁老哥临时结拜、强行组队！大家凑成一个乌合之众，啊不，正义联盟，浩浩荡荡杀向光明顶！ "
                "讲什么武道规矩，咱们主打一个仗势欺人！冲上去不为除魔卫道，就为了抢魔教食堂那口祖传的大铁锅！打赢了分行李，不对，分战利品，打输了，大不了就地躺下投降，反正魔教包吃包住！抄家伙，上啊！"
            ),
            "player_limit": arena_coop_core.ARENA_COOP_PLAYER_LIMIT,
            "guest_limit_per_entry": arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY,
            "daily_limit": arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT,
            "registration_hint": "两面五行旗互护并庇护教主，携带多目标招式可更快破阵；张无忌血量降低时会升级攻势。",
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
                (
                    "比赛方式",
                    "与其他玩家组成15人队伍，共同击败明教众人，获胜还有神秘奖励哦。",
                ),
            ),
        },
        "arena_coop_active_entry": active_entry,
        "arena_coop_recruiting_event": recruiting_event,
        "arena_coop_available_guests": available_guests,
        "arena_coop_selected_guest_ids": selected_guest_ids,
    }


def get_arena_registration_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context["arena_registration_layout"] = True
    context.update(get_arena_coop_summary_context(manor))
    context.update(get_arena_wheel_summary_context(manor))
    active_entry = get_active_entry(manor)

    context["active_entry"] = active_entry
    recruiting_tournament = (
        ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
        .annotate(entry_count=Count("entries"))
        .order_by("created_at")
        .first()
    )
    context["recruiting_tournament"] = recruiting_tournament
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


def get_arena_wheel_summary_context(manor: Manor) -> dict:
    from datetime import timedelta

    from django.utils import timezone

    from gameplay.services.arena import wheel_core

    current_time = timezone.now()
    local_now = timezone.localtime(current_time)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    today_count = (
        ArenaWheelEntry.objects.filter(manor=manor, joined_at__gte=day_start, joined_at__lt=day_end)
        .exclude(status=ArenaWheelEntry.Status.CANCELLED)
        .count()
    )
    remaining_daily = max(0, wheel_core.ARENA_DAILY_PARTICIPATION_LIMIT - today_count)
    active_entry = (
        ArenaWheelEntry.objects.select_related("event")
        .prefetch_related("entry_guests__guest")
        .filter(
            manor=manor,
            status__in=[ArenaWheelEntry.Status.REGISTERED, ArenaWheelEntry.Status.ACTIVE],
            event__status__in=[
                ArenaWheelEvent.Status.RECRUITING,
                ArenaWheelEvent.Status.PREPARING,
                ArenaWheelEvent.Status.RUNNING,
            ],
        )
        .order_by("-joined_at", "-id")
        .first()
    )
    recruiting_event = (
        ArenaWheelEvent.objects.filter(status=ArenaWheelEvent.Status.RECRUITING)
        .annotate(
            registered_entry_count=Count(
                "entries",
                filter=Q(entries__status=ArenaWheelEntry.Status.REGISTERED),
            )
        )
        .first()
    )
    available_guests = manor.guests.none()
    selected_guest_ids: set[int] = set()
    if active_entry:
        selected_guest_ids = set(active_entry.entry_guests.values_list("guest_id", flat=True))
    elif remaining_daily > 0:
        available_guests = (
            manor.guests.select_related("template").filter(status=GuestStatus.IDLE).order_by("-level", "id")
        )

    return {
        "arena_wheel_today_participations": today_count,
        "arena_wheel_remaining_daily": remaining_daily,
        "arena_wheel_daily_limit": wheel_core.ARENA_DAILY_PARTICIPATION_LIMIT,
        "arena_wheel_event": {
            "name": "金羽擂台",
            "subtitle": "",
            "description": (
                "江湖护院大会临时加开擂台：六位玩家随机分入甲、乙两队，各队出战顺序也交给签筒决定（签筒今日不收贿赂）。"
                "每位玩家派出十名门客轮番迎战；胜者保留剩余血量，每隔 5 分钟继续守擂。"
                "每次连胜后都会累积“久战疲敝”，敏捷、智力、武力、防御各降低 5%，最多叠加 2 层——久战虽显英雄气，腿软也是真的。战至一队无人可战，另一队获胜。"
            ),
            "player_limit": wheel_core.ARENA_WHEEL_PLAYER_LIMIT,
            "guest_limit_per_entry": wheel_core.ARENA_WHEEL_MAX_GUESTS_PER_ENTRY,
            "prepare_duration_seconds": wheel_core.ARENA_WHEEL_PREPARE_DURATION_SECONDS,
            "daily_limit": wheel_core.ARENA_DAILY_PARTICIPATION_LIMIT,
            "registration_hint": "需选择 10 名空闲门客；满 6 人后随机分队并进入准备期。",
            "summary_metrics": build_summary_metrics(
                ("报名人数", f"{wheel_core.ARENA_WHEEL_PLAYER_LIMIT} 人满员开战"),
                ("参战门客", f"每人 {wheel_core.ARENA_WHEEL_MAX_GUESTS_PER_ENTRY} 名"),
                (
                    "队伍奖励",
                    f"胜方 {wheel_core.ARENA_WHEEL_WIN_REWARD} / 败方 {wheel_core.ARENA_WHEEL_LOSS_REWARD} 角斗币",
                ),
                (
                    "比赛方式",
                    "随机分队与出战顺序，胜者保留剩余血量；每次连胜后敏捷、智力、武力、防御各降低 5%，最多叠加 2 层，直至一队无人可战。",
                ),
            ),
        },
        "arena_wheel_active_entry": active_entry,
        "arena_wheel_recruiting_event": recruiting_event,
        "arena_wheel_available_guests": available_guests,
        "arena_wheel_selected_guest_ids": selected_guest_ids,
        "arena_wheel_can_afford_registration": manor.silver >= wheel_core.ARENA_REGISTRATION_SILVER_COST,
        "arena_wheel_registration_silver_cost": wheel_core.ARENA_REGISTRATION_SILVER_COST,
    }


def get_arena_exchange_context(manor: Manor) -> dict:
    context = build_common_context(manor)
    context["reward_rows"] = build_reward_rows(manor)
    context["recent_exchange_records"] = ArenaExchangeRecord.objects.filter(manor=manor).order_by("-created_at")[:15]
    context["chunqiu_coin_exchange"] = get_arena_currency_exchange_status(manor)
    return context
