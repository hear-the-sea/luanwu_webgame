"""
帮会成员管理视图：申请、审批、职位、解散
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.utils.rate_limit import rate_limit_redirect

from ..decorators import require_guild_leader, require_guild_manager, require_guild_member
from ..models import Guild
from ..services import guild as guild_service
from ..services import member as member_service
from .helpers import (
    build_guild_member_context,
    execute_guild_action,
    get_manageable_member,
    get_reviewable_application,
    load_active_member_summary,
    load_pending_applications,
)


def _upgrade_guild_and_get_level(member, operator) -> int:
    guild_service.upgrade_guild(member.guild, operator)
    member.guild.refresh_from_db(fields=["level"])
    return int(member.guild.level)


@login_required
@rate_limit_redirect("guild_apply", limit=5, window_seconds=60)
def apply_to_guild(request, guild_id):
    """申请加入帮会"""
    guild = get_object_or_404(Guild, id=guild_id, is_active=True)

    if request.method == "POST":
        message = request.POST.get("message", "").strip()

        outcome = execute_guild_action(
            request,
            action=lambda: member_service.apply_to_guild(user=request.user, guild=guild, message=message),
            success_message="已提交申请，请等待审批",
        )
        if outcome.succeeded:
            return redirect("guilds:detail", guild_id=guild.id)

    context = {
        "guild": guild,
    }

    return render(request, "guilds/apply.html", context)


@login_required
@require_guild_manager
def application_list(request):
    """申请列表"""
    member = request.guild_member
    applications = load_pending_applications(member.guild)
    member_count = member.guild.current_member_count

    context = build_guild_member_context(
        member,
        applications=applications,
        pending_count=len(applications),
        member_count=member_count,
        is_guild_full=member_count >= member.guild.member_capacity,
    )

    return render(request, "guilds/applications.html", context)


@login_required
@require_guild_manager
@require_POST
@rate_limit_redirect("guild_approve", limit=20, window_seconds=60)
def approve_application(request, app_id):
    """通过申请"""
    member = request.guild_member

    application = get_reviewable_application(member.guild, app_id)

    execute_guild_action(
        request,
        action=lambda: member_service.approve_application(application, request.user),
        success_message=lambda _result: f"已通过{application.applicant.manor.display_name}的申请",
    )

    return redirect("guilds:applications")


@login_required
@require_guild_manager
@require_POST
@rate_limit_redirect("guild_reject", limit=20, window_seconds=60)
def reject_application(request, app_id):
    """拒绝申请"""
    member = request.guild_member

    application = get_reviewable_application(member.guild, app_id)
    note = request.POST.get("note", "")

    execute_guild_action(
        request,
        action=lambda: member_service.reject_application(application, request.user, note),
        success_message=lambda _result: f"已拒绝{application.applicant.manor.display_name}的申请",
    )

    return redirect("guilds:applications")


@login_required
@require_guild_member
def member_list(request):
    """成员列表"""
    member = request.guild_member
    guild = member.guild
    member_summary = load_active_member_summary(guild)
    leader = member_summary.leader

    context = build_guild_member_context(
        member,
        members=member_summary.members,
        leader=leader,
        member_count=member_summary.member_count,
        leader_count=1 if leader else 0,
        admin_count=member_summary.admin_count,
        normal_member_count=member_summary.normal_member_count,
    )

    return render(request, "guilds/members.html", context)


@login_required
@require_guild_manager
@require_POST
@rate_limit_redirect("guild_kick", limit=10, window_seconds=60)
def kick_member(request, member_id):
    """辞退成员"""
    # 安全修复：在查询时就过滤帮会，防止信息泄露
    target_member = get_manageable_member(request.guild_member.guild_id, member_id)

    execute_guild_action(
        request,
        action=lambda: member_service.kick_member(target_member, request.user),
        success_message=lambda _result: f"已辞退{target_member.user.username}",
    )

    return redirect("guilds:members")


@login_required
@require_guild_leader
@require_POST
@rate_limit_redirect("guild_appoint", limit=10, window_seconds=60)
def appoint_admin(request, member_id):
    """任命管理员"""
    # 安全修复：在查询时就过滤帮会，防止信息泄露
    target_member = get_manageable_member(request.guild_member.guild_id, member_id)

    execute_guild_action(
        request,
        action=lambda: member_service.appoint_admin(target_member, request.user),
        success_message=lambda _result: f"已任命{target_member.user.username}为管理员",
    )

    return redirect("guilds:members")


@login_required
@require_guild_leader
@require_POST
@rate_limit_redirect("guild_demote", limit=10, window_seconds=60)
def demote_admin(request, member_id):
    """罢免管理员"""
    # 安全修复：在查询时就过滤帮会，防止信息泄露
    target_member = get_manageable_member(request.guild_member.guild_id, member_id)

    execute_guild_action(
        request,
        action=lambda: member_service.demote_admin(target_member, request.user),
        success_message=lambda _result: f"已罢免{target_member.user.username}的管理员职位",
    )

    return redirect("guilds:members")


@login_required
@require_guild_leader  # 安全修复：只有帮主才能转让，改用 require_guild_leader
@require_POST
@rate_limit_redirect("guild_transfer", limit=5, window_seconds=60)
def transfer_leadership(request, member_id):
    """转让帮主"""
    current_leader = request.guild_member
    # 安全修复：在查询时就过滤帮会，防止跨帮会转让
    new_leader = get_manageable_member(current_leader.guild_id, member_id)

    execute_guild_action(
        request,
        action=lambda: member_service.transfer_leadership(current_leader, new_leader),
        success_message=lambda _result: f"已将帮主之位传给{new_leader.user.username}",
    )

    return redirect("guilds:members")


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_leave", limit=5, window_seconds=60)
def leave_guild(request):
    """退出帮会"""
    member = request.guild_member

    outcome = execute_guild_action(
        request,
        action=lambda: member_service.leave_guild(member),
        success_message="已退出帮会",
    )
    if outcome.succeeded:
        return redirect("guilds:hall")
    return redirect("guilds:detail", guild_id=member.guild.id)


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_upgrade", limit=5, window_seconds=60)
def upgrade_guild(request):
    """升级帮会"""
    member = request.guild_member

    execute_guild_action(
        request,
        action=lambda: _upgrade_guild_and_get_level(member, request.user),
        success_message=lambda level: f"帮会升级成功！当前等级：{level}",
    )

    return redirect("guilds:detail", guild_id=member.guild.id)


@login_required
@require_guild_member
@require_POST
@rate_limit_redirect("guild_disband", limit=2, window_seconds=300)
def disband_guild(request):
    """解散帮会"""
    member = request.guild_member
    guild = member.guild

    # 验证确认
    confirm_name = request.POST.get("confirm_name", "")
    if confirm_name != guild.name:
        messages.error(request, "请输入正确的帮会名称以确认解散")
        return redirect("guilds:detail", guild_id=guild.id)

    outcome = execute_guild_action(
        request,
        action=lambda: guild_service.disband_guild(guild, request.user),
        success_message="帮会已解散",
    )
    if outcome.succeeded:
        return redirect("guilds:hall")
    return redirect("guilds:detail", guild_id=guild.id)
