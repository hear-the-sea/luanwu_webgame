from datetime import timedelta

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import User
from gameplay.models import ItemTemplate
from guilds.admin import GuildAdmin
from guilds.models import Guild, GuildMember
from trade.admin import AuctionRoundAdmin
from trade.models import AuctionRound, AuctionSlot


@pytest.mark.django_db
@pytest.mark.parametrize("round_count", [1, 5])
def test_auction_round_admin_counts_use_one_query(round_count, django_assert_num_queries):
    item = ItemTemplate.objects.create(key="admin_query_item", name="后台查询测试物品")
    now = timezone.now()
    expected = {}
    for index in range(round_count):
        auction_round = AuctionRound.objects.create(
            round_number=index + 1,
            status=AuctionRound.Status.COMPLETED,
            start_at=now - timedelta(days=1),
            end_at=now,
        )
        statuses = [AuctionSlot.Status.SOLD, AuctionSlot.Status.SOLD, AuctionSlot.Status.UNSOLD]
        for status in statuses:
            AuctionSlot.objects.create(
                round=auction_round,
                item_template=item,
                status=status,
                starting_price=1,
                current_price=1,
            )
        expected[auction_round.pk] = (3, 2)
    empty_round = AuctionRound.objects.create(
        round_number=round_count + 1,
        status=AuctionRound.Status.COMPLETED,
        start_at=now - timedelta(days=1),
        end_at=now,
    )
    expected[empty_round.pk] = (0, 0)
    model_admin = AuctionRoundAdmin(AuctionRound, AdminSite())
    request = RequestFactory().get("/admin/trade/auctionround/")

    with django_assert_num_queries(1):
        actual = {
            obj.pk: (model_admin.get_slot_count(obj), model_admin.get_sold_count(obj))
            for obj in model_admin.get_queryset(request)
        }
    assert actual == expected

    # 额外关联筛选产生重复行时，统计仍须准确。
    with django_assert_num_queries(1):
        filtered = {
            obj.pk: (model_admin.get_slot_count(obj), model_admin.get_sold_count(obj))
            for obj in model_admin.get_queryset(request).filter(slots__quantity__gte=1)
        }
    assert filtered == {pk: counts for pk, counts in expected.items() if pk != empty_round.pk}


@pytest.mark.django_db
@pytest.mark.parametrize("guild_count", [1, 5])
def test_guild_admin_active_member_counts_use_one_query(guild_count, django_assert_num_queries):
    expected = {}
    for index in range(guild_count):
        guild = Guild.objects.create(name=f"查询帮会{index}")
        for member_index, is_active in enumerate([True, True, False]):
            user = User.objects.create(username=f"admin_count_{index}_{member_index}")
            GuildMember.objects.create(guild=guild, user=user, is_active=is_active)
        expected[guild.pk] = 2
    empty_guild = Guild.objects.create(name="查询空帮会")
    expected[empty_guild.pk] = 0
    model_admin = GuildAdmin(Guild, AdminSite())
    request = RequestFactory().get("/admin/guilds/guild/")

    with django_assert_num_queries(1):
        actual = {obj.pk: obj.current_member_count for obj in model_admin.get_queryset(request)}
    assert actual == expected

    with django_assert_num_queries(1):
        filtered = {
            obj.pk: obj.current_member_count
            for obj in model_admin.get_queryset(request).filter(members__is_active=True)
        }
    assert filtered == {pk: count for pk, count in expected.items() if pk != empty_guild.pk}
