from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from gameplay.models import Manor
from gameplay.services.buildings.forge import load_blueprint_catalog
from gameplay.services.inventory.core import add_item_to_inventory_locked
from guilds.constants import load_guild_rules
from guilds.models import GuildBlueprintRewardClaim, GuildMember

WEEKLY_BLUEPRINT_CAPS = {"blue": 5, "purple": 2, "orange": 1}


def current_week_start():
    today = timezone.localdate()
    return today - timedelta(days=today.weekday())


def _current_week_start_at() -> datetime:
    return timezone.make_aware(datetime.combine(current_week_start(), datetime.min.time()))


@transaction.atomic
def claim_guild_blueprint_reward(member: GuildMember, blueprint_key: str) -> GuildBlueprintRewardClaim:
    # Keep the same Manor -> GuildMember order as other guild write paths.
    # Inventory capacity and the weekly claim both depend on these rows being
    # serialized together.
    locked_manor = Manor.objects.select_for_update().get(user_id=member.user_id)
    locked_member = GuildMember.objects.select_for_update().get(pk=member.pk)
    if locked_member.user_id != locked_manor.user_id:
        raise ValueError("帮会成员与庄园不匹配")
    if not locked_member.is_active or locked_member.weekly_contribution <= 0:
        raise ValueError("本周贡献不足，不能领取帮会图纸")

    catalog_entry = load_blueprint_catalog().get(str(blueprint_key or "").strip())
    if not catalog_entry or catalog_entry.rarity not in WEEKLY_BLUEPRINT_CAPS:
        raise ValueError("帮会图纸配置无效")
    allowed = load_guild_rules()["blueprint_rewards"]["choices"].get(catalog_entry.rarity, [])
    if catalog_entry.key not in allowed:
        raise ValueError("该图纸不在本期帮会奖励池")

    used = GuildBlueprintRewardClaim.objects.filter(
        member=locked_member,
        rarity=catalog_entry.rarity,
        claimed_at__gte=_current_week_start_at(),
    ).count()
    cap = WEEKLY_BLUEPRINT_CAPS[catalog_entry.rarity]
    if used >= cap:
        raise ValueError(f"本周{catalog_entry.rarity}图纸领取次数已达上限")

    add_item_to_inventory_locked(locked_manor, catalog_entry.key, 1)
    return GuildBlueprintRewardClaim.objects.create(
        member=locked_member,
        blueprint_key=catalog_entry.key,
        rarity=catalog_entry.rarity,
    )
