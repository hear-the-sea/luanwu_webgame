# guilds/services/member.py

import logging
from collections.abc import Callable

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from core.exceptions import GuildMembershipError, GuildPermissionError
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS

from ..models import Guild, GuildApplication, GuildMember
from .guild import create_announcement
from .hero_pool import invalidate_member_hero_pool
from .member_notifications import resolve_display_name, send_system_message_to_user
from .utils import get_active_membership, lock_active_member_for_guild

logger = logging.getLogger(__name__)


def _log_followup_failure(action, **context):
    context_str = " ".join(f"{key}={value}" for key, value in context.items())
    if context_str:
        logger.warning("Guild %s follow-up failed: %s", action, context_str, exc_info=True)
    else:
        logger.warning("Guild %s follow-up failed", action, exc_info=True)


def _run_followup(action, callback, **context):
    try:
        callback()
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS:
        _log_followup_failure(action, **context)


def _run_message_followup(
    action: str,
    *,
    user_id: int,
    title: str,
    body: str,
    guild_name: str,
    **context,
):
    _run_followup(
        action,
        lambda: send_system_message_to_user(
            user_id,
            title=title,
            body=body,
            action=action,
            guild_name=guild_name,
            logger=logger,
        ),
        guild_name=guild_name,
        **context,
    )


def _run_announcement_followup(action: str, *, guild, content: str, **context):
    _run_followup(
        action,
        lambda: create_announcement(guild, "system", content),
        **context,
    )


def _schedule_followup_after_commit(callback: Callable[[], None]) -> None:
    transaction.on_commit(callback)


def _approve_application_state(application, reviewer, auto=False):
    with transaction.atomic():
        # 锁定申请行，避免并发重复处理
        application_locked = (
            GuildApplication.objects.select_for_update().select_related("guild", "applicant").get(pk=application.pk)
        )
        if application_locked.status != "pending":
            raise GuildMembershipError("申请已被处理")

        guild = application_locked.guild

        # 锁定帮会行，串行化审批，避免并发超员
        guild_locked = Guild.objects.select_for_update().get(pk=guild.pk)

        # 验证权限（非自动审批时）
        if not auto:
            membership = get_active_membership(guild_locked, reviewer, "您没有审批权限")
            membership = lock_active_member_for_guild(membership, error_msg="您没有审批权限")
            if not membership.can_manage:
                raise GuildPermissionError("您没有审批权限")

        # 在锁内检查是否满员
        current_count = GuildMember.objects.filter(guild=guild_locked, is_active=True).count()
        if current_count >= guild_locked.member_capacity:
            raise GuildMembershipError("帮会已满员")

        # 验证申请人是否已加入其他帮会
        existing_membership = (
            GuildMember.objects.select_for_update().filter(user=application_locked.applicant, is_active=True).first()
        )
        if existing_membership:
            raise GuildMembershipError("申请人已加入其他帮会")

        # 更新申请状态
        application_locked.status = "approved"
        application_locked.reviewed_by = reviewer
        application_locked.reviewed_at = timezone.now()
        application_locked.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        # 创建或更新成员记录
        # 由于GuildMember.user是OneToOneField，需要处理已存在的记录
        member_record = GuildMember.objects.select_for_update().filter(user=application_locked.applicant).first()
        if member_record:
            member_record.guild = guild_locked
            member_record.position = "member"
            member_record.is_active = True
            member_record.joined_at = timezone.now()
            member_record.left_at = None
            member_record.save(update_fields=["guild", "position", "is_active", "joined_at", "left_at"])
        else:
            try:
                GuildMember.objects.create(
                    guild=guild_locked,
                    user=application_locked.applicant,
                    position="member",
                )
            except IntegrityError:
                raise GuildMembershipError("申请人已加入其他帮会")

        return guild_locked, application_locked.applicant_id, guild_locked.name


def _send_approve_application_followups(guild, applicant_user_id: int, guild_name: str):
    display_name = resolve_display_name(applicant_user_id)
    _run_message_followup(
        "approve",
        user_id=applicant_user_id,
        title="入帮申请通过",
        body=f"您的入帮申请已通过，欢迎加入【{guild_name}】！",
        guild_name=guild_name,
        applicant_user_id=applicant_user_id,
    )
    _run_announcement_followup(
        "approve announcement",
        guild=guild,
        content=f"欢迎新成员{display_name}加入帮会！",
        applicant_user_id=applicant_user_id,
        guild_name=guild_name,
    )


def _reject_application_state(application, reviewer, note=""):
    with transaction.atomic():
        application_locked = (
            GuildApplication.objects.select_for_update().select_related("guild", "applicant").get(pk=application.pk)
        )
        if application_locked.status != "pending":
            raise GuildMembershipError("申请已被处理")

        membership = get_active_membership(application_locked.guild, reviewer, "您没有审批权限")
        membership = lock_active_member_for_guild(membership, error_msg="您没有审批权限")
        if not membership.can_manage:
            raise GuildPermissionError("您没有审批权限")

        application_locked.status = "rejected"
        application_locked.reviewed_by = reviewer
        application_locked.reviewed_at = timezone.now()
        application_locked.review_note = note
        application_locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])

        return application_locked.applicant_id, application_locked.guild.name


def _send_reject_application_followups(applicant_user_id: int, guild_name: str, note: str):
    _run_message_followup(
        "reject",
        user_id=applicant_user_id,
        title="入帮申请被拒绝",
        body=f"您的入帮申请被拒绝。\n拒绝原因：{note if note else '无'}",
        guild_name=guild_name,
        applicant_user_id=applicant_user_id,
    )


def _leave_guild_state(member):
    if not member.is_active:
        raise GuildMembershipError("您不在帮会中")

    if member.is_leader:
        raise GuildPermissionError("帮主无法直接退出，请先转让帮主或解散帮会")

    guild = member.guild
    with transaction.atomic():
        member_locked = lock_active_member_for_guild(member, error_msg="您不在帮会中")

        member_locked.is_active = False
        member_locked.left_at = timezone.now()
        member_locked.save(update_fields=["is_active", "left_at"])
        invalidate_member_hero_pool(member_locked)

        return guild, member_locked.user_id, guild.name


def _send_leave_guild_followups(guild, member_user_id: int, guild_name: str):
    display_name = resolve_display_name(member_user_id)
    _run_announcement_followup(
        "leave announcement",
        guild=guild,
        content=f"成员{display_name}离开了帮会。",
        member_user_id=member_user_id,
        guild_name=guild_name,
    )


def _kick_member_state(target_member, operator):
    operator_member = get_active_membership(target_member.guild, operator, "您没有辞退权限")
    if not operator_member.can_manage:
        raise GuildPermissionError("您没有辞退权限")
    if target_member.position in ["leader", "admin"]:
        raise GuildPermissionError("无法辞退帮主或管理员")
    if target_member.user == operator:
        raise GuildPermissionError("无法辞退自己")

    guild = target_member.guild
    with transaction.atomic():
        operator_member = lock_active_member_for_guild(operator_member, error_msg="您没有辞退权限")
        if not operator_member.can_manage:
            raise GuildPermissionError("您没有辞退权限")

        target_locked = lock_active_member_for_guild(target_member, error_msg="该成员已不在帮会中")
        if target_locked.position in ["leader", "admin"]:
            raise GuildPermissionError("无法辞退帮主或管理员")
        if target_locked.user_id == operator.id:
            raise GuildPermissionError("无法辞退自己")

        target_locked.is_active = False
        target_locked.left_at = timezone.now()
        target_locked.save(update_fields=["is_active", "left_at"])
        invalidate_member_hero_pool(target_locked)

        return guild, target_locked.user_id, guild.name


def _send_kick_member_followups(guild, target_user_id: int, guild_name: str):
    display_name = resolve_display_name(target_user_id)
    _run_message_followup(
        "kick",
        user_id=target_user_id,
        title="被移出帮会",
        body=f"您已被移出帮会【{guild_name}】。",
        guild_name=guild_name,
        target_user_id=target_user_id,
    )
    _run_announcement_followup(
        "kick announcement",
        guild=guild,
        content=f"成员{display_name}被移出帮会。",
        target_user_id=target_user_id,
        guild_name=guild_name,
    )


def _appoint_admin_state(target_member, operator):
    operator_member = get_active_membership(target_member.guild, operator, "只有帮主可以任命管理员")
    if not operator_member.is_leader:
        raise GuildPermissionError("只有帮主可以任命管理员")
    if target_member.position != "member":
        raise GuildMembershipError("该成员已是管理人员")

    guild = target_member.guild
    with transaction.atomic():
        guild_locked = Guild.objects.select_for_update().get(pk=target_member.guild_id)
        operator_member = lock_active_member_for_guild(operator_member, error_msg="只有帮主可以任命管理员")
        if not operator_member.is_leader:
            raise GuildPermissionError("只有帮主可以任命管理员")
        admin_count = guild_locked.members.filter(is_active=True, position="admin").count()
        if admin_count >= 2:
            raise GuildMembershipError("管理员数量已达上限（2人）")

        target_locked = lock_active_member_for_guild(target_member, error_msg="该成员已离开帮会")
        if target_locked.position != "member":
            raise GuildMembershipError("该成员已是管理人员")

        target_locked.position = "admin"
        target_locked.save(update_fields=["position"])

        return guild, guild.name, operator.id, target_locked.user_id


def _send_appoint_admin_followups(guild, guild_name: str, operator_user_id: int, target_user_id: int):
    operator_display_name = resolve_display_name(operator_user_id)
    target_display_name = resolve_display_name(target_user_id)
    _run_message_followup(
        "appoint-admin",
        user_id=target_user_id,
        title="职位变更",
        body=f"您已被任命为帮会【{guild_name}】的管理员！",
        guild_name=guild_name,
        target_user_id=target_user_id,
    )
    _run_announcement_followup(
        "appoint-admin announcement",
        guild=guild,
        content=f"{operator_display_name}任命{target_display_name}为管理员。",
        operator_user_id=operator_user_id,
        target_user_id=target_user_id,
        guild_name=guild_name,
    )


def _demote_admin_state(target_member, operator):
    operator_member = get_active_membership(target_member.guild, operator, "只有帮主可以罢免管理员")
    if not operator_member.is_leader:
        raise GuildPermissionError("只有帮主可以罢免管理员")
    if target_member.position != "admin":
        raise GuildMembershipError("该成员不是管理员")

    guild = target_member.guild
    with transaction.atomic():
        operator_member = lock_active_member_for_guild(operator_member, error_msg="只有帮主可以罢免管理员")
        if not operator_member.is_leader:
            raise GuildPermissionError("只有帮主可以罢免管理员")
        target_locked = lock_active_member_for_guild(target_member, error_msg="该成员已离开帮会")
        if target_locked.position != "admin":
            raise GuildMembershipError("该成员不是管理员")
        target_locked.position = "member"
        target_locked.save(update_fields=["position"])

        return guild, guild.name, target_locked.user_id


def _send_demote_admin_followups(guild, guild_name: str, target_user_id: int):
    target_display_name = resolve_display_name(target_user_id)
    _run_message_followup(
        "demote-admin",
        user_id=target_user_id,
        title="职位变更",
        body="您已被罢免管理员职位，降为普通成员。",
        guild_name=guild_name,
        target_user_id=target_user_id,
    )
    _run_announcement_followup(
        "demote-admin announcement",
        guild=guild,
        content=f"{target_display_name}卸任管理员职位。",
        target_user_id=target_user_id,
        guild_name=guild_name,
    )


def _transfer_leadership_state(current_leader_member, new_leader_member):
    if not current_leader_member.is_leader:
        raise GuildPermissionError("您不是帮主")
    if new_leader_member.guild_id != current_leader_member.guild_id:
        raise GuildMembershipError("只能转让给本帮会成员")
    if not new_leader_member.is_active:
        raise GuildMembershipError("该成员已离开帮会")

    guild = current_leader_member.guild
    with transaction.atomic():
        current_locked = lock_active_member_for_guild(current_leader_member, error_msg="您不是帮主")
        new_locked = lock_active_member_for_guild(new_leader_member, error_msg="该成员已离开帮会")

        if not current_locked.is_leader:
            raise GuildPermissionError("您不是帮主")
        if not new_locked.is_active:
            raise GuildMembershipError("该成员已离开帮会")

        current_locked.position = "member"
        current_locked.save(update_fields=["position"])
        new_locked.position = "leader"
        new_locked.save(update_fields=["position"])

        return guild, guild.name, current_locked.user_id, new_locked.user_id


def _send_transfer_leadership_followups(guild, guild_name: str, current_leader_user_id: int, new_leader_user_id: int):
    current_leader_display_name = resolve_display_name(current_leader_user_id)
    new_leader_display_name = resolve_display_name(new_leader_user_id)
    _run_message_followup(
        "transfer-leadership",
        user_id=new_leader_user_id,
        title="职位变更",
        body=f"您已成为帮会【{guild_name}】的新任帮主！",
        guild_name=guild_name,
        new_leader_user_id=new_leader_user_id,
    )
    _run_announcement_followup(
        "transfer-leadership announcement",
        guild=guild,
        content=f"{current_leader_display_name}将帮主之位传给了{new_leader_display_name}！",
        current_leader_user_id=current_leader_user_id,
        new_leader_user_id=new_leader_user_id,
        guild_name=guild_name,
    )


def apply_to_guild(user, guild, message=""):
    """
    申请加入帮会

    Args:
        user: 申请人User对象
        guild: Guild对象
        message: 申请留言

    Returns:
        GuildApplication对象

    Raises:
        GuildMembershipError: 验证失败
    """
    # 验证用户是否已加入帮会
    if hasattr(user, "guild_membership") and user.guild_membership.is_active:
        raise GuildMembershipError("您已加入帮会")

    # 验证帮会是否已满员
    if guild.is_full:
        raise GuildMembershipError("帮会已满员")

    # 验证是否已有待审批的申请
    existing = GuildApplication.objects.filter(guild=guild, applicant=user, status="pending").exists()
    if existing:
        raise GuildMembershipError("您已有待审批的申请")

    # 创建申请；数据库约束兜底处理并发重复 pending 申请。
    try:
        with transaction.atomic():
            application = GuildApplication.objects.create(
                guild=guild,
                applicant=user,
                message=message,
                status="pending",
            )
    except IntegrityError:
        raise GuildMembershipError("您已有待审批的申请")

    # 如果设置了自动接受，直接通过
    if guild.auto_accept:
        approve_application(application, None, auto=True)

    return application


def approve_application(application, reviewer, auto=False):
    """
    通过申请

    Args:
        application: GuildApplication对象
        reviewer: 审批人User对象（auto=True时可为None）
        auto: 是否自动审批

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    guild, applicant_user_id, guild_name = _approve_application_state(application, reviewer, auto=auto)
    _schedule_followup_after_commit(lambda: _send_approve_application_followups(guild, applicant_user_id, guild_name))


def reject_application(application, reviewer, note=""):
    """
    拒绝申请

    Args:
        application: GuildApplication对象
        reviewer: 审批人User对象
        note: 拒绝原因

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    applicant_user_id, guild_name = _reject_application_state(application, reviewer, note=note)
    _schedule_followup_after_commit(lambda: _send_reject_application_followups(applicant_user_id, guild_name, note))


def leave_guild(member):
    """
    退出帮会

    Args:
        member: GuildMember对象

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    guild, member_user_id, guild_name = _leave_guild_state(member)
    _schedule_followup_after_commit(lambda: _send_leave_guild_followups(guild, member_user_id, guild_name))


def kick_member(target_member, operator):
    """
    辞退成员

    Args:
        target_member: 被辞退的GuildMember对象
        operator: 操作者User对象

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    guild, target_user_id, guild_name = _kick_member_state(target_member, operator)
    _schedule_followup_after_commit(lambda: _send_kick_member_followups(guild, target_user_id, guild_name))


def appoint_admin(target_member, operator):
    """
    任命管理员

    Args:
        target_member: GuildMember对象
        operator: 操作者User对象

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    guild, guild_name, operator_user_id, target_user_id = _appoint_admin_state(target_member, operator)
    _schedule_followup_after_commit(
        lambda: _send_appoint_admin_followups(guild, guild_name, operator_user_id, target_user_id)
    )


def demote_admin(target_member, operator):
    """
    罢免管理员

    Args:
        target_member: GuildMember对象
        operator: 操作者User对象

    Raises:
        GuildMembershipError | GuildPermissionError: 验证失败
    """
    guild, guild_name, target_user_id = _demote_admin_state(target_member, operator)
    _schedule_followup_after_commit(lambda: _send_demote_admin_followups(guild, guild_name, target_user_id))


def transfer_leadership(current_leader_member, new_leader_member):
    """
    转让帮主

    Args:
        current_leader_member: 当前帮主GuildMember对象
        new_leader_member: 新帮主GuildMember对象

    Raises:
        ValueError: 验证失败
    """
    guild, guild_name, current_leader_user_id, new_leader_user_id = _transfer_leadership_state(
        current_leader_member,
        new_leader_member,
    )
    _schedule_followup_after_commit(
        lambda: _send_transfer_leadership_followups(
            guild,
            guild_name,
            current_leader_user_id,
            new_leader_user_id,
        )
    )


def get_member_rankings(guild, ranking_type="total"):
    """
    获取成员排行榜

    Args:
        guild: Guild对象
        ranking_type: 'total'(总贡献) 或 'weekly'(本周贡献)

    Returns:
        QuerySet
    """
    members = guild.members.filter(is_active=True).select_related("user", "user__manor")

    if ranking_type == "weekly":
        return members.order_by("-weekly_contribution", "-total_contribution")[:10]
    else:
        return members.order_by("-total_contribution", "-joined_at")[:10]
