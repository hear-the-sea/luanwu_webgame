from __future__ import annotations

import pytest
from django.db.utils import DatabaseError

from core.exceptions import GuildPermissionError, GuildValidationError
from guilds.models import Guild, GuildMember, GuildRaidRun
from guilds.services import guild as guild_service

pytest_plugins = ("tests.guilds.fixtures",)


@pytest.mark.django_db
class TestGuildInfoUpdate:
    def test_update_guild_info_success(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="信息帮会", description="旧简介")

        updated_guild = guild_service.update_guild_info(
            guild=guild,
            operator=user_with_gold_bars,
            description="  新简介  ",
            auto_accept=True,
        )

        updated_guild.refresh_from_db()
        assert updated_guild.description == "新简介"
        assert updated_guild.auto_accept is True

    def test_non_leader_cannot_update_guild_info(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="权限帮会", description="旧简介")
        GuildMember.objects.create(guild=guild, user=second_user, position="member")

        with pytest.raises(GuildPermissionError, match="帮主"):
            guild_service.update_guild_info(
                guild=guild,
                operator=second_user,
                description="越权简介",
                auto_accept=True,
            )


@pytest.mark.django_db
class TestGuildDisband:
    def test_disband_guild(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="解散帮会", description="")
        GuildMember.objects.create(guild=guild, user=second_user, position="member")

        guild_service.disband_guild(guild, user_with_gold_bars)
        guild.refresh_from_db()

        assert guild.is_active is False
        for member in guild.members.all():
            assert member.is_active is False

    def test_disband_guild_keeps_success_when_followup_message_fails(
        self, user_with_gold_bars, second_user, monkeypatch
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="稳健解散帮会", description="")
        GuildMember.objects.create(guild=guild, user=second_user, position="member")

        def _raise_bulk(*_args, **_kwargs):
            raise DatabaseError("message backend down")

        monkeypatch.setattr("guilds.services.guild.bulk_create_messages", _raise_bulk)

        guild_service.disband_guild(guild, user_with_gold_bars)
        guild.refresh_from_db()
        assert guild.is_active is False

    def test_disband_guild_followup_programming_error_bubbles_up(self, user_with_gold_bars, second_user, monkeypatch):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="解散契约帮会", description="")
        GuildMember.objects.create(guild=guild, user=second_user, position="member")

        monkeypatch.setattr(
            "guilds.services.guild.bulk_create_messages",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guild disband message contract")),
        )

        with pytest.raises(AssertionError, match="broken guild disband message contract"):
            guild_service.disband_guild(guild, user_with_gold_bars)

        guild.refresh_from_db()
        assert guild.is_active is False

    def test_non_leader_cannot_disband(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="安全帮会", description="")
        GuildMember.objects.create(guild=guild, user=second_user, position="member")

        with pytest.raises(GuildPermissionError, match="帮主"):
            guild_service.disband_guild(guild, second_user)

    @pytest.mark.parametrize(
        "run_status",
        [
            GuildRaidRun.Status.MARCHING,
            GuildRaidRun.Status.BATTLING,
            GuildRaidRun.Status.RETURNING,
            GuildRaidRun.Status.RETREATED,
        ],
    )
    def test_disband_guild_rejects_all_in_flight_raid_states(
        self,
        user_with_gold_bars,
        second_user,
        run_status,
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="出征门禁帮会", description="")
        attacker_guild = Guild.objects.create(name="关联进攻帮会", founder=second_user)
        attacker_member = GuildMember.objects.create(
            guild=attacker_guild,
            user=second_user,
            position="leader",
        )
        GuildRaidRun.objects.create(
            attacker_guild=attacker_guild,
            defender_guild=guild,
            started_by=attacker_member,
            status=run_status,
        )

        with pytest.raises(GuildValidationError, match="存在进行中的帮会对战"):
            guild_service.disband_guild(guild, user_with_gold_bars)

        guild.refresh_from_db()
        assert guild.is_active is True
