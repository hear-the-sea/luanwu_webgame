from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from django.contrib import messages
from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from core.exceptions import GameError
from guilds import constants as guild_constants
from guilds.models import Guild, GuildAnnouncement, GuildApplication, GuildMember

ActionResultT = TypeVar("ActionResultT")


@dataclass(frozen=True)
class GuildActionOutcome(Generic[ActionResultT]):
    succeeded: bool
    result: ActionResultT | None = None


def build_guild_member_context(member: GuildMember, **extra: object) -> dict[str, object]:
    return {
        "guild": member.guild,
        "member": member,
        **extra,
    }


def execute_guild_action(
    request: HttpRequest,
    *,
    action: Callable[[], ActionResultT],
    success_message: str | Callable[[ActionResultT], str],
    error_message_formatter: Callable[[Exception], str] = str,
) -> GuildActionOutcome[ActionResultT]:
    try:
        result = action()
    except GameError as exc:
        messages.error(request, error_message_formatter(exc))
        return GuildActionOutcome(succeeded=False)

    resolved_success_message = success_message(result) if callable(success_message) else success_message
    messages.success(request, resolved_success_message)
    return GuildActionOutcome(succeeded=True, result=result)


@dataclass(frozen=True)
class GuildMemberSummary:
    members: list[GuildMember]
    leader: GuildMember | None
    member_count: int
    admin_count: int
    normal_member_count: int


def load_active_member_summary(guild: Guild) -> GuildMemberSummary:
    members = list(
        guild.members.filter(is_active=True).select_related("user__manor").order_by("-position", "-total_contribution")
    )

    leader = None
    admin_count = 0
    for guild_member in members:
        if guild_member.position == "leader" and leader is None:
            leader = guild_member
            continue
        if guild_member.position == "admin":
            admin_count += 1

    member_count = len(members)
    normal_member_count = max(0, member_count - admin_count - (1 if leader else 0))
    return GuildMemberSummary(
        members=members,
        leader=leader,
        member_count=member_count,
        admin_count=admin_count,
        normal_member_count=normal_member_count,
    )


def load_pending_applications(guild: Guild) -> list[GuildApplication]:
    return list(
        GuildApplication.objects.filter(guild=guild, status="pending")
        .select_related("applicant")
        .order_by("-created_at")
    )


def load_guild_leader(guild: Guild) -> GuildMember | None:
    return guild.members.filter(is_active=True, position="leader").select_related("user__manor").first()


def load_recent_announcements(guild: Guild, *, limit: int = 5) -> list[GuildAnnouncement]:
    return list(guild.announcements.select_related("author__manor").order_by("-created_at", "-id")[:limit])


def load_ordered_technologies(guild: Guild) -> list[Any]:
    supported_keys = guild_constants.get_supported_guild_technology_keys()
    return list(guild.technologies.filter(tech_key__in=supported_keys).order_by("category", "tech_key"))


def load_donation_logs(guild: Guild, *, limit: int = 50) -> list[Any]:
    return list(guild.donation_logs.select_related("member__user__manor").order_by("-donated_at")[:limit])


def load_resource_logs(guild: Guild, *, limit: int = 50) -> list[Any]:
    return list(guild.resource_logs.select_related("related_user").order_by("-created_at")[:limit])


def get_reviewable_application(guild: Guild, app_id: int) -> GuildApplication:
    return get_object_or_404(
        GuildApplication.objects.select_related("applicant"),
        id=app_id,
        guild=guild,
    )


def get_manageable_member(guild_id: int, member_id: int) -> GuildMember:
    return get_object_or_404(
        GuildMember.objects.select_related("user"),
        id=member_id,
        guild_id=guild_id,
    )
