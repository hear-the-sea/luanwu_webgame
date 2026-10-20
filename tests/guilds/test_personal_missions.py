from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import GuildMembershipError, GuildValidationError
from gameplay.models import Manor, MissionRun, MissionTemplate
from guilds.models import Guild, GuildMember, GuildPersonalMissionParticipation, GuildResourceLog, GuildWarehouse
from guilds.services import personal_missions

pytestmark = pytest.mark.django_db


def _make_member(django_user_model, suffix: str):
    user = django_user_model.objects.create_user(username=f"personal_guild_{suffix}", password="test-password")
    manor = Manor.objects.get(user=user)
    guild = Guild.objects.create(name=f"疑案帮会{suffix}", founder=user, silver=0)
    member = GuildMember.objects.create(guild=guild, user=user)
    return user, manor, guild, member


def _make_mission(key: str, difficulty: str, contribution: int) -> MissionTemplate:
    mission, _ = MissionTemplate.objects.update_or_create(
        key=key,
        defaults={
            "name": f"任务{key}",
            "difficulty": difficulty,
            "guest_only": True,
            "drop_table": {"guild_contribution": contribution},
        },
    )
    return mission


def _make_run(manor: Manor, mission: MissionTemplate) -> MissionRun:
    return MissionRun.objects.create(
        manor=manor,
        mission=mission,
        return_at=timezone.now() + timedelta(hours=1),
    )


def test_personal_guild_mission_requires_membership(django_user_model):
    user = django_user_model.objects.create_user(username="personal_guild_no_member", password="test-password")
    manor = Manor.objects.get(user=user)
    mission = _make_mission("guild_personal_zhenlong_qipu", "junior", 100)
    run = _make_run(manor, mission)

    with pytest.raises(GuildMembershipError, match="加入帮会"):
        with transaction.atomic():
            personal_missions.register_personal_guild_mission_run(mission, manor, run)

    assert not GuildPersonalMissionParticipation.objects.filter(mission_run=run).exists()


def test_personal_guild_mission_attempt_limit_is_shared_across_tiers(django_user_model):
    user, manor, _guild, _member = _make_member(django_user_model, "shared_limit")
    junior = _make_mission("guild_personal_zhenlong_qipu", "junior", 100)
    intermediate = _make_mission("guild_personal_shaolin_night_shadow", "intermediate", 300)

    with transaction.atomic():
        for index in range(10):
            mission = junior if index % 2 == 0 else intermediate
            run = _make_run(manor, mission)
            personal_missions.register_personal_guild_mission_run(mission, manor, run)

    assert personal_missions.get_personal_guild_mission_status(user)["remaining"] == 0
    assert GuildPersonalMissionParticipation.objects.filter(user=user).count() == 10

    blocked_run = _make_run(manor, junior)
    with pytest.raises(GuildValidationError, match="共享 10 次"):
        with transaction.atomic():
            personal_missions.register_personal_guild_mission_run(junior, manor, blocked_run)


def test_personal_guild_mission_settles_guild_warehouse_and_member_contribution(django_user_model):
    _user, manor, guild, member = _make_member(django_user_model, "settlement")
    mission = _make_mission("guild_personal_yanziwu_secret_letter", "advanced", 600)
    run = _make_run(manor, mission)
    with transaction.atomic():
        personal_missions.register_personal_guild_mission_run(mission, manor, run)

    drops = {
        "silver": 280000,
        "grain": 350000,
        "red_ruby": 4,
        "guild_contribution": 600,
        "experience_fruit": 3,
    }
    with transaction.atomic():
        deposited = personal_missions.settle_personal_guild_mission_drops(run, drops)

    guild.refresh_from_db()
    member.refresh_from_db()
    assert deposited == {
        "silver": 280000,
        "grain": 350000,
        "red_ruby": 4,
        "guild_contribution": 600,
    }
    assert drops == {"experience_fruit": 3}
    assert guild.silver == 280000
    assert member.total_contribution == 600
    assert member.current_contribution == 600
    assert member.weekly_contribution == 600
    assert GuildWarehouse.objects.get(guild=guild, item_key="grain").quantity == 350000
    assert GuildWarehouse.objects.get(guild=guild, item_key="red_ruby").quantity == 4
    assert GuildResourceLog.objects.filter(guild=guild, action="mission_reward").count() == 1


def test_personal_guild_mission_does_not_credit_contribution_to_a_new_guild(django_user_model):
    _user, manor, original_guild, member = _make_member(django_user_model, "transferred")
    mission = _make_mission("guild_personal_zhenlong_qipu", "junior", 100)
    run = _make_run(manor, mission)
    with transaction.atomic():
        personal_missions.register_personal_guild_mission_run(mission, manor, run)

    new_guild = Guild.objects.create(name="新帮会transferred", founder=member.user, silver=0)
    member.guild = new_guild
    member.current_contribution = 0
    member.total_contribution = 0
    member.weekly_contribution = 0
    member.save(
        update_fields=[
            "guild",
            "current_contribution",
            "total_contribution",
            "weekly_contribution",
        ]
    )
    drops = {
        "silver": 40000,
        "grain": 50000,
        "guild_contribution": 100,
    }

    with transaction.atomic():
        deposited = personal_missions.settle_personal_guild_mission_drops(run, drops)

    member.refresh_from_db()
    original_guild.refresh_from_db()
    new_guild.refresh_from_db()
    log = GuildResourceLog.objects.get(guild=original_guild, action="mission_reward")
    assert deposited == {"silver": 40000, "grain": 50000}
    assert drops == {}
    assert original_guild.silver == 40000
    assert new_guild.silver == 0
    assert member.total_contribution == 0
    assert member.current_contribution == 0
    assert "贡献未记入" in log.note


def test_personal_guild_mission_does_not_credit_contribution_after_member_leaves(django_user_model):
    _user, manor, guild, member = _make_member(django_user_model, "left_before_settlement")
    mission = _make_mission("guild_personal_zhenlong_qipu", "junior", 100)
    run = _make_run(manor, mission)
    with transaction.atomic():
        personal_missions.register_personal_guild_mission_run(mission, manor, run)

    # 退出与踢出保留成员记录，只更新活跃状态和退出时间。
    member.is_active = False
    member.left_at = timezone.now()
    member.save(update_fields=["is_active", "left_at"])
    drops = {"silver": 40000, "grain": 50000, "guild_contribution": 100}

    with transaction.atomic():
        deposited = personal_missions.settle_personal_guild_mission_drops(run, drops)

    member.refresh_from_db()
    guild.refresh_from_db()
    assert deposited == {"silver": 40000, "grain": 50000}
    assert drops == {}
    assert guild.silver == 40000
    assert member.total_contribution == 0
    assert member.current_contribution == 0
    assert member.weekly_contribution == 0
    assert "贡献未记入" in GuildResourceLog.objects.get(guild=guild, action="mission_reward").note
