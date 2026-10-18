from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor
from gameplay.services.resources import load_resource_production_bases, settle_resource_production_locked
from gameplay.utils.resource_calculator import get_hourly_rates, get_personnel_grain_cost_per_hour
from guilds.models import Guild, GuildMember, GuildTechnology, GuildWarehouse
from guilds.services.technology import get_guild_resource_bonuses_for_users
from guilds.services.warehouse_config import get_technology_production_items
from guilds.tasks import process_single_guild_production
from guilds.technology_tree import PRODUCTION_TECH_KEYS, TECHNOLOGY_NODE_BY_KEY

pytestmark = pytest.mark.django_db


def _prepare_old_rate_grain_interval(monkeypatch, manor, now):
    monkeypatch.setattr(timezone, "now", lambda: now)
    monkeypatch.setattr("gameplay.services.resources.scale_value", lambda value: value)
    manor.refresh_from_db()
    manor.grain_capacity = 1_000_000
    manor.resource_updated_at = now - timedelta(hours=1)
    manor.save(update_fields=["grain_capacity", "resource_updated_at"])
    starting_grain = manor.grain
    old_rate = get_hourly_rates(manor)["grain"] - get_personnel_grain_cost_per_hour(manor)
    expected_gain = int(old_rate)
    assert expected_gain > 0
    return starting_grain, expected_gain


@pytest.fixture
def guild_world(django_user_model):
    user = django_user_model.objects.create_user(username="technology_effect_audit")
    guild = Guild.objects.create(name="科技实效审计", founder=user, is_active=True, active_technology_route="")
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    return guild, member


@pytest.mark.parametrize("level", [1, 2, 3, 5, 10])
def test_all_production_nodes_reach_warehouse_once_per_date(guild_world, monkeypatch, level):
    guild, _member = guild_world
    expected = Counter()
    daily_expected = Counter()
    interval_expected = Counter()
    for key in PRODUCTION_TECH_KEYS:
        effective = min(level, TECHNOLOGY_NODE_BY_KEY[key]["max_level"])
        GuildTechnology.objects.create(guild=guild, tech_key=key, level=effective)
        outputs = get_technology_production_items(key, effective)
        assert outputs, f"{key} {effective}级缺少产出配置"
        output = {item.item_key: item.quantity for item in outputs}
        expected.update(output)
        if TECHNOLOGY_NODE_BY_KEY[key]["production_interval_days"] == 1:
            daily_expected.update(output)
        else:
            interval_expected.update(output)
    now = timezone.now()
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    process_single_guild_production.run(guild.pk)
    assert dict(GuildWarehouse.objects.filter(guild=guild).values_list("item_key", "quantity")) == dict(expected)
    assert GuildTechnology.objects.filter(guild=guild, last_production_at__isnull=True).count() == 0
    process_single_guild_production.run(guild.pk)
    assert dict(GuildWarehouse.objects.filter(guild=guild).values_list("item_key", "quantity")) == dict(expected)
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now + timedelta(days=1))
    process_single_guild_production.run(guild.pk)
    expected.update(daily_expected)
    assert dict(GuildWarehouse.objects.filter(guild=guild).values_list("item_key", "quantity")) == dict(expected)
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now + timedelta(days=3))
    process_single_guild_production.run(guild.pk)
    expected.update(daily_expected)
    expected.update(interval_expected)
    assert dict(GuildWarehouse.objects.filter(guild=guild).values_list("item_key", "quantity")) == dict(expected)


def test_inactive_guild_and_unresearched_production_do_not_create_items(guild_world):
    guild, _member = guild_world
    GuildTechnology.objects.create(guild=guild, tech_key="hero_rebirth_card", level=0)
    process_single_guild_production.run(guild.pk)
    assert not GuildWarehouse.objects.filter(guild=guild).exists()
    GuildTechnology.objects.filter(guild=guild).update(level=3)
    Guild.objects.filter(pk=guild.pk).update(is_active=False)
    process_single_guild_production.run(guild.pk)
    assert not GuildWarehouse.objects.filter(guild=guild).exists()


@pytest.mark.parametrize("level", range(1, 11))
def test_forging_equipment_uses_legacy_boxes_once_per_day(guild_world, level):
    from guilds.services.warehouse import get_technology_daily_output_display
    from guilds.services.warehouse_config import get_production_items

    guild, _member = guild_world
    GuildTechnology.objects.create(guild=guild, tech_key="forging_equipment_forge", level=level, max_level=10)
    GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=level, max_level=10)
    expected = {
        item.item_key: (item.quantity, item.contribution_cost) for item in get_production_items("equipment", level)
    }
    process_single_guild_production.run(guild.pk)
    process_single_guild_production.run(guild.pk)
    actual = {
        item.item_key: (item.quantity, item.contribution_cost) for item in GuildWarehouse.objects.filter(guild=guild)
    }
    assert actual == expected
    display = get_technology_daily_output_display("forging_equipment_forge", level)
    assert f"帮会绿装箱 ×{2 if level > 1 else 1}" in display
    assert GuildTechnology.objects.get(guild=guild, tech_key="equipment_forge").last_production_at is None


@pytest.mark.parametrize(
    "level, expected",
    [
        (1, {"experience_fruit": 500, "experience_peach_recipe": 20}),
        (2, {"experience_fruit": 1000, "experience_peach_recipe": 20, "experience_pineapple_recipe": 20}),
        (
            3,
            {
                "experience_fruit": 1500,
                "experience_peach_recipe": 20,
                "experience_pineapple_recipe": 20,
                "experience_watermelon_recipe": 20,
            },
        ),
    ],
)
def test_hero_fruit_daily_output_matches_each_requested_tier(guild_world, level, expected):
    from guilds.services.warehouse import get_technology_daily_output_display

    guild, _member = guild_world
    GuildTechnology.objects.create(guild=guild, tech_key="hero_fruit_blueprint", level=level, max_level=3)
    process_single_guild_production.run(guild.pk)
    assert dict(GuildWarehouse.objects.filter(guild=guild).values_list("item_key", "quantity")) == expected
    fruit = GuildWarehouse.objects.get(guild=guild, item_key="experience_fruit")
    assert fruit.contribution_cost == 1
    display = get_technology_daily_output_display("hero_fruit_blueprint", level)
    assert f"经验果 ×{expected['experience_fruit']}" in display
    assert "经验桃子合成图纸 ×20" in display


@pytest.mark.parametrize("level", [1, 2, 3])
def test_sheep_resource_bonus_reaches_single_and_batch_rates(guild_world, level):
    guild, member = guild_world
    manor = ensure_manor(member.user)
    baseline = get_hourly_rates(manor)
    assert baseline["grain"] > 0 and baseline["silver"] > 0
    for key in ("sheep_farmland", "sheep_silver_mine"):
        GuildTechnology.objects.create(guild=guild, tech_key=key, level=level)
    boosted = get_hourly_rates(manor)
    batch = dict(load_resource_production_bases([manor])[manor.pk].hourly_rates)
    for resource in ("grain", "silver"):
        assert boosted[resource] == pytest.approx(baseline[resource] * (1 + 0.05 * level))
        assert batch[resource] == pytest.approx(boosted[resource])
    member.is_active = False
    member.save(update_fields=["is_active"])
    assert get_hourly_rates(manor) == baseline


def test_grain_bonus_does_not_raise_silver_and_inactive_guild_has_no_bonus(guild_world):
    guild, member = guild_world
    manor = ensure_manor(member.user)
    baseline = get_hourly_rates(manor)
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_farmland", level=3)
    boosted = get_hourly_rates(manor)
    assert boosted["grain"] == pytest.approx(baseline["grain"] * 1.15)
    assert boosted["silver"] == baseline["silver"]
    Guild.objects.filter(pk=guild.pk).update(is_active=False)
    assert get_hourly_rates(manor) == baseline


def test_sheep_technology_upgrade_settles_members_at_the_previous_rate(guild_world, monkeypatch):
    from guilds.models import GuildWarehouse
    from guilds.services.technology import upgrade_technology

    guild, member = guild_world
    manor = ensure_manor(member.user)
    Guild.objects.filter(pk=guild.pk).update(silver=100_000_000, active_technology_route="sheep")
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_farmland", category="sheep", level=1, max_level=3)
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=10_000_000)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=10_000)
    now = timezone.now() + timedelta(seconds=1)
    starting_grain, expected_gain = _prepare_old_rate_grain_interval(monkeypatch, manor, now)

    upgrade_technology(guild, "sheep_farmland", member.user)

    manor.refresh_from_db()
    assert manor.grain == starting_grain + expected_gain
    assert manor.resource_updated_at == now
    assert GuildTechnology.objects.get(guild=guild, tech_key="sheep_farmland").level == 2


def test_sheep_route_reset_settles_members_before_removing_their_bonus(guild_world, monkeypatch):
    from guilds.services.technology import reset_technology_route

    guild, member = guild_world
    manor = ensure_manor(member.user)
    Guild.objects.filter(pk=guild.pk).update(active_technology_route="sheep")
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_farmland", category="sheep", level=1, max_level=3)
    now = timezone.now() + timedelta(seconds=1)
    starting_grain, expected_gain = _prepare_old_rate_grain_interval(monkeypatch, manor, now)

    reset_technology_route(guild, "sheep", member.user)

    manor.refresh_from_db()
    assert manor.grain == starting_grain + expected_gain
    assert manor.resource_updated_at == now
    assert GuildTechnology.objects.get(guild=guild, tech_key="sheep_farmland").level == 0


def test_leaving_guild_settles_member_at_the_previous_rate(guild_world, monkeypatch, django_user_model):
    from guilds.services.member import _leave_guild_state

    guild, _leader = guild_world
    user = django_user_model.objects.create_user(username="technology_effect_leaving_member")
    member = GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)
    manor = ensure_manor(user)
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_farmland", category="sheep", level=2, max_level=3)
    now = timezone.now() + timedelta(seconds=1)
    starting_grain, expected_gain = _prepare_old_rate_grain_interval(monkeypatch, manor, now)

    _leave_guild_state(member)

    manor.refresh_from_db()
    member.refresh_from_db()
    assert manor.grain == starting_grain + expected_gain
    assert manor.resource_updated_at == now
    assert member.is_active is False


def test_resource_bonus_bulk_lookup_uses_one_query(guild_world, django_assert_num_queries, django_user_model):
    guild, member = guild_world
    second = django_user_model.objects.create_user(username="technology_effect_second")
    GuildMember.objects.create(guild=guild, user=second, is_active=True)
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_farmland", level=3)
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_silver_mine", level=2)
    with django_assert_num_queries(1):
        bonuses = get_guild_resource_bonuses_for_users((member.user_id, second.pk))
    for user_id in (member.user_id, second.pk):
        assert bonuses[user_id]["grain"] == pytest.approx(0.15)
        assert bonuses[user_id]["silver"] == pytest.approx(0.10)


@pytest.mark.parametrize("use_batch_basis", [False, True])
def test_guild_silver_bonus_is_credited_by_resource_settlement(guild_world, use_batch_basis):
    guild, member = guild_world
    manor = ensure_manor(member.user)
    baseline = get_hourly_rates(manor)["silver"]
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_silver_mine", level=3)
    now = timezone.now()
    manor.silver = 0
    manor.silver_capacity = 1000000
    manor.resource_updated_at = now - timedelta(hours=1)
    manor.save(update_fields=["silver", "silver_capacity", "resource_updated_at"])
    basis = load_resource_production_bases([manor])[manor.pk] if use_batch_basis else None
    with transaction.atomic():
        settled = settle_resource_production_locked(manor, now=now, production_basis=basis, production_buffs=())
    manor.refresh_from_db()
    assert settled["silver"] == int(baseline * 1.15)
    assert manor.silver == settled["silver"]
