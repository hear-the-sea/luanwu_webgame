"""
帮会服务层通用辅助函数
"""

from core.exceptions import GuildMembershipError

from ..models import Guild, GuildMember


def lock_active_member_for_guild(
    member: GuildMember,
    *,
    error_msg: str = "您不在帮会中",
    model: type[GuildMember] = GuildMember,
) -> GuildMember:
    """
    在事务内重新锁定成员并复核其仍属于原帮会。

    视图装饰器和事务外权限检查只用于快速失败；所有写路径必须在锁内
    重新确认成员仍活跃，避免旧请求在离帮或换帮后继续写入。
    """
    member_pk = getattr(member, "pk", None)
    guild_id = getattr(member, "guild_id", None)
    if guild_id is None:
        guild_id = getattr(getattr(member, "guild", None), "pk", None)
    if member_pk is None or guild_id is None:
        raise GuildMembershipError(error_msg)

    locked_member = (
        model.objects.select_for_update()
        .select_related("guild", "user")
        .filter(pk=member_pk, guild_id=guild_id, is_active=True)
        .first()
    )
    if locked_member is None:
        raise GuildMembershipError(error_msg)
    return locked_member


def get_active_membership(guild: Guild, user, error_msg: str = "您不是该帮会成员") -> GuildMember:
    """
    获取用户在指定帮会的有效成员记录

    Args:
        guild: 帮会对象
        user: 用户对象
        error_msg: 找不到成员时的错误消息

    Returns:
        GuildMember对象

    Raises:
        GuildMembershipError: 用户不是该帮会的活跃成员
    """
    try:
        return GuildMember.objects.get(guild=guild, user=user, is_active=True)
    except GuildMember.DoesNotExist:
        raise GuildMembershipError(error_msg)
