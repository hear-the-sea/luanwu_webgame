from __future__ import annotations

import logging

import pytest
from django.db import transaction
from django.db.utils import DatabaseError, IntegrityError
from django.test import TestCase

from core.exceptions import GuildMembershipError, GuildPermissionError, MessageError
from guilds.models import Guild, GuildApplication, GuildMember
from guilds.services import guild as guild_service
from guilds.services import member as member_service
from guilds.services import member_notifications

pytest_plugins = ("tests.guilds.fixtures",)


@pytest.mark.django_db
class TestGuildMembership:
    def test_apply_and_approve(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="招人帮会", description="")

        application = member_service.apply_to_guild(second_user, guild, "请收留我")
        assert application.status == "pending"

        member_service.approve_application(application, user_with_gold_bars)
        application.refresh_from_db()

        assert application.status == "approved"
        membership = GuildMember.objects.get(user=second_user, guild=guild)
        assert membership.is_active is True
        assert membership.position == "member"

    def test_direct_orm_rejects_second_pending_application_for_same_guild_and_user(
        self, user_with_gold_bars, second_user
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="唯一申请帮会", description="")
        GuildApplication.objects.create(guild=guild, applicant=second_user, status="pending")

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                GuildApplication.objects.create(guild=guild, applicant=second_user, status="pending")

    def test_apply_to_guild_maps_pending_application_integrity_conflict_to_business_error(
        self, user_with_gold_bars, second_user, monkeypatch
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="竞态申请帮会", description="")

        def _raise_integrity_error(**_kwargs):
            raise IntegrityError("duplicate pending application")

        monkeypatch.setattr(GuildApplication.objects, "create", _raise_integrity_error)

        with pytest.raises(GuildMembershipError, match="您已有待审批的申请"):
            member_service.apply_to_guild(second_user, guild, "请收留我")

    def test_approve_keeps_success_when_followup_notification_fails(
        self, user_with_gold_bars, second_user, monkeypatch
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="稳健审批帮会", description="")
        application = member_service.apply_to_guild(second_user, guild, "请收留我")

        def _raise_message(*_args, **_kwargs):
            raise MessageError("message backend down")

        def _raise_announcement(*_args, **_kwargs):
            raise DatabaseError("announcement backend down")

        monkeypatch.setattr(member_service, "send_system_message_to_user", _raise_message)
        monkeypatch.setattr(member_service, "create_announcement", _raise_announcement)

        member_service.approve_application(application, user_with_gold_bars)
        application.refresh_from_db()

        assert application.status == "approved"
        membership = GuildMember.objects.get(user=second_user, guild=guild)
        assert membership.is_active is True

    def test_approve_application_rechecks_reviewer_permission_inside_transaction(
        self, user_with_gold_bars, second_user, monkeypatch
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="审批竞态帮会", description="")
        reviewer_member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        application = member_service.apply_to_guild(second_user, guild, "请收留我")
        real_get_active_membership = member_service.get_active_membership

        def _demote_after_initial_check(*args, **kwargs):
            resolved = real_get_active_membership(*args, **kwargs)
            GuildMember.objects.filter(pk=reviewer_member.pk).update(position="member")
            return resolved

        monkeypatch.setattr("guilds.services.member.get_active_membership", _demote_after_initial_check)

        with pytest.raises(GuildPermissionError, match="您没有审批权限"):
            member_service.approve_application(application, user_with_gold_bars)

        application.refresh_from_db()
        reviewer_member.refresh_from_db()
        assert application.status == "pending"
        assert reviewer_member.position == "leader"
        assert GuildMember.objects.filter(user=second_user).exists() is False

    def test_approve_application_defers_followups_until_outer_commit(
        self, user_with_gold_bars, second_user, monkeypatch
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="延迟通知帮会", description="")
        application = member_service.apply_to_guild(second_user, guild, "请收留我")
        followups: list[tuple[str, int]] = []

        monkeypatch.setattr(
            member_service,
            "send_system_message_to_user",
            lambda user_id, **_kwargs: followups.append(("message", user_id)),
        )
        monkeypatch.setattr(
            member_service,
            "create_announcement",
            lambda guild_obj, _author, _content: followups.append(("announcement", guild_obj.pk)),
        )

        with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
            member_service.approve_application(application, user_with_gold_bars)

        application.refresh_from_db()
        assert application.status == "approved"
        assert followups == []
        assert len(callbacks) == 1

        callbacks[0]()
        assert ("message", second_user.id) in followups
        assert ("announcement", guild.id) in followups

    @pytest.mark.parametrize(
        ("operation", "guild_name"),
        [
            ("reject_application", "拒申稳健会"),
            ("leave_guild", "离帮稳健会"),
            ("kick_member", "踢人稳健会"),
        ],
    )
    def test_membership_mutation_keeps_success_when_followup_notifications_fail(
        self, user_with_gold_bars, second_user, monkeypatch, operation, guild_name
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name=guild_name, description="")

        def _raise_followup(*_args, **_kwargs):
            raise DatabaseError("notification backend down")

        monkeypatch.setattr(member_service, "send_system_message_to_user", _raise_followup)
        monkeypatch.setattr(member_service, "create_announcement", _raise_followup)

        if operation == "reject_application":
            application = member_service.apply_to_guild(second_user, guild, "请收留我")

            member_service.reject_application(application, user_with_gold_bars, note="名额已满")
            application.refresh_from_db()

            assert application.status == "rejected"
            assert application.review_note == "名额已满"
            return

        member = GuildMember.objects.create(guild=guild, user=second_user, position="member")

        if operation == "leave_guild":
            member_service.leave_guild(member)
        else:
            member_service.kick_member(member, user_with_gold_bars)

        member.refresh_from_db()
        assert member.is_active is False
        assert member.left_at is not None

    def test_apply_to_full_guild(self, user_with_gold_bars, second_user, django_user_model):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="小帮会", description="")

        for index in range(9):
            user = django_user_model.objects.create_user(username=f"filler_{index}", password="pass12345")
            from gameplay.services.manor.core import ensure_manor

            ensure_manor(user)
            GuildMember.objects.create(guild=guild, user=user, position="member")

        with pytest.raises(GuildMembershipError, match="已满员"):
            member_service.apply_to_guild(second_user, guild, "")

    def test_kick_member(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="踢人帮会", description="")
        target_member = GuildMember.objects.create(guild=guild, user=second_user, position="member")

        member_service.kick_member(target_member, user_with_gold_bars)

        target_member.refresh_from_db()
        assert target_member.is_active is False
        assert target_member.left_at is not None

    def test_kick_member_rechecks_operator_permission_inside_transaction(
        self, user_with_gold_bars, second_user, monkeypatch
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="辞退竞态帮会", description="")
        operator_member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        target_member = GuildMember.objects.create(guild=guild, user=second_user, position="member")
        real_get_active_membership = member_service.get_active_membership

        def _demote_after_initial_check(*args, **kwargs):
            resolved = real_get_active_membership(*args, **kwargs)
            GuildMember.objects.filter(pk=operator_member.pk).update(position="member")
            return resolved

        monkeypatch.setattr("guilds.services.member.get_active_membership", _demote_after_initial_check)

        with pytest.raises(GuildPermissionError, match="您没有辞退权限"):
            member_service.kick_member(target_member, user_with_gold_bars)

        target_member.refresh_from_db()
        operator_member.refresh_from_db()
        assert target_member.is_active is True
        assert operator_member.position == "member"

    def test_kick_member_rejects_target_moved_to_another_guild(self, user_with_gold_bars, second_user):
        old_guild = guild_service.create_guild(user=user_with_gold_bars, name="旧辞退帮会", description="")
        target_member = GuildMember.objects.create(guild=old_guild, user=second_user, position="member")
        new_guild = Guild.objects.create(name="新辞退帮会", founder=user_with_gold_bars, is_active=True)
        GuildMember.objects.filter(pk=target_member.pk).update(guild=new_guild)

        with pytest.raises(GuildMembershipError, match="该成员已不在帮会中"):
            member_service.kick_member(target_member, user_with_gold_bars)

        target_member.refresh_from_db()
        assert target_member.guild_id == new_guild.id
        assert target_member.is_active is True

    def test_appoint_admin_rejects_target_that_left_after_initial_check(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="任命竞态帮会", description="")
        target_member = GuildMember.objects.create(guild=guild, user=second_user, position="member")
        GuildMember.objects.filter(pk=target_member.pk).update(is_active=False, left_at=None)

        with pytest.raises(GuildMembershipError, match="该成员已离开帮会"):
            member_service.appoint_admin(target_member, user_with_gold_bars)

        target_member.refresh_from_db()
        assert target_member.is_active is False
        assert target_member.position == "member"

    def test_leave_guild(self, user_with_gold_bars, second_user):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="退出帮会", description="")
        member = GuildMember.objects.create(guild=guild, user=second_user, position="member")

        member_service.leave_guild(member)

        member.refresh_from_db()
        assert member.is_active is False
        assert member.left_at is not None

    def test_leave_guild_rejects_member_moved_to_another_guild(self, user_with_gold_bars, second_user):
        old_guild = guild_service.create_guild(user=user_with_gold_bars, name="旧退出帮会", description="")
        stale_member = GuildMember.objects.create(guild=old_guild, user=second_user, position="member")
        new_guild = Guild.objects.create(name="新退出帮会", founder=user_with_gold_bars, is_active=True)
        GuildMember.objects.filter(pk=stale_member.pk).update(guild=new_guild)

        with pytest.raises(GuildMembershipError, match="您不在帮会中"):
            member_service.leave_guild(stale_member)

        stale_member.refresh_from_db()
        assert stale_member.guild_id == new_guild.id
        assert stale_member.is_active is True

    def test_leader_cannot_leave(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="帮主帮会", description="")

        leader_member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)

        with pytest.raises(GuildPermissionError, match="帮主"):
            member_service.leave_guild(leader_member)


@pytest.mark.django_db
def test_send_system_message_to_user_returns_false_when_manor_missing(monkeypatch, caplog):
    class _EmptyQuerySet:
        @staticmethod
        def first():
            return None

    monkeypatch.setattr(member_notifications.Manor.objects, "filter", lambda **_kwargs: _EmptyQuerySet())

    with caplog.at_level(logging.WARNING):
        result = member_notifications.send_system_message_to_user(
            9999,
            title="系统消息",
            body="正文",
            action="approve_application",
            guild_name="测试帮会",
            logger=logging.getLogger("tests.guilds.member_notifications"),
        )

    assert result is False
    assert any("manor missing" in record.getMessage() for record in caplog.records)


@pytest.mark.django_db
def test_send_system_message_to_user_returns_false_when_create_message_fails(second_user, monkeypatch, caplog):
    def _boom(**_kwargs):
        raise MessageError("message backend down")

    monkeypatch.setattr(member_notifications, "create_message", _boom)

    with caplog.at_level(logging.WARNING):
        result = member_notifications.send_system_message_to_user(
            second_user.id,
            title="系统消息",
            body="正文",
            action="approve_application",
            guild_name="测试帮会",
            logger=logging.getLogger("tests.guilds.member_notifications"),
        )

    assert result is False
    assert any("follow-up message failed" in record.getMessage() for record in caplog.records)


@pytest.mark.django_db
def test_send_system_message_to_user_runtime_marker_error_bubbles_up(second_user, monkeypatch):
    monkeypatch.setattr(
        member_notifications,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        member_notifications.send_system_message_to_user(
            second_user.id,
            title="系统消息",
            body="正文",
            action="approve_application",
            guild_name="测试帮会",
            logger=logging.getLogger("tests.guilds.member_notifications"),
        )


@pytest.mark.django_db
def test_approve_application_followup_programming_error_bubbles_up(user_with_gold_bars, second_user, monkeypatch):
    guild = guild_service.create_guild(user=user_with_gold_bars, name="公会消息契约帮会", description="")
    application = member_service.apply_to_guild(second_user, guild, "请收留我")

    monkeypatch.setattr(member_service, "send_system_message_to_user", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        member_service,
        "create_announcement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guild announcement contract")),
    )

    with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
        member_service.approve_application(application, user_with_gold_bars)

    assert len(callbacks) == 1
    with pytest.raises(AssertionError, match="broken guild announcement contract"):
        callbacks[0]()

    application.refresh_from_db()
    assert application.status == "approved"
    membership = GuildMember.objects.get(user=second_user, guild=guild)
    assert membership.is_active is True
