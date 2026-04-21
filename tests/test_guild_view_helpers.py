import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from core.exceptions import GuildContributionError
from gameplay.services.manor.core import ensure_manor
from guilds.models import (
    Guild,
    GuildAnnouncement,
    GuildApplication,
    GuildDonationLog,
    GuildMember,
    GuildResourceLog,
    GuildTechnology,
)
from guilds.views.helpers import (
    build_guild_member_context,
    execute_guild_action,
    get_manageable_member,
    get_reviewable_application,
    load_active_member_summary,
    load_donation_logs,
    load_guild_leader,
    load_ordered_technologies,
    load_pending_applications,
    load_recent_announcements,
    load_resource_logs,
)

User = get_user_model()


def _attach_session_and_messages(request):
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


def _create_user(username: str):
    user = User.objects.create_user(username=username, password="pass123")
    ensure_manor(user)
    return user


@pytest.mark.django_db
def test_build_guild_member_context_merges_extra_values():
    leader_user = _create_user("guild_context_leader")
    guild = Guild.objects.create(name="上下文帮", founder=leader_user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=leader_user, position="leader")

    context = build_guild_member_context(member, logs=[1, 2, 3])

    assert context["guild"] == guild
    assert context["member"] == member
    assert context["logs"] == [1, 2, 3]


@pytest.mark.django_db
def test_execute_guild_action_adds_success_message():
    request = RequestFactory().post("/guilds/test/")
    _attach_session_and_messages(request)

    outcome = execute_guild_action(
        request,
        action=lambda: "done",
        success_message=lambda result: f"成功：{result}",
    )

    assert outcome.succeeded is True
    assert outcome.result == "done"
    assert [str(message) for message in get_messages(request)] == ["成功：done"]


@pytest.mark.django_db
def test_execute_guild_action_legacy_value_error_bubbles_up():
    request = RequestFactory().post("/guilds/test/")
    _attach_session_and_messages(request)

    with pytest.raises(ValueError, match="原始错误"):
        execute_guild_action(
            request,
            action=lambda: (_ for _ in ()).throw(ValueError("原始错误")),
            success_message="不会出现",
            error_message_formatter=lambda exc: f"格式化：{exc}",
        )

    assert [str(message) for message in get_messages(request)] == []


@pytest.mark.django_db
def test_execute_guild_action_formats_game_error_message():
    request = RequestFactory().post("/guilds/test/")
    _attach_session_and_messages(request)

    outcome = execute_guild_action(
        request,
        action=lambda: (_ for _ in ()).throw(GuildContributionError("贡献不足")),
        success_message="不会出现",
        error_message_formatter=lambda exc: f"格式化：{exc}",
    )

    assert outcome.succeeded is False
    assert outcome.result is None
    assert [str(message) for message in get_messages(request)] == ["格式化：贡献不足"]


@pytest.mark.django_db
def test_load_active_member_summary_uses_single_member_query_and_preloads_manor():
    leader_user = _create_user("guild_summary_leader")
    admin_user = _create_user("guild_summary_admin")
    member_user = _create_user("guild_summary_member")

    guild = Guild.objects.create(name="查询优化帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    GuildMember.objects.create(guild=guild, user=admin_user, position="admin")
    GuildMember.objects.create(guild=guild, user=member_user, position="member")

    with CaptureQueriesContext(connection) as captured:
        summary = load_active_member_summary(guild)

    guild_member_queries = [q for q in captured.captured_queries if 'from "guild_members"' in q["sql"].lower()]
    assert len(guild_member_queries) == 1
    assert summary.member_count == 3
    assert summary.admin_count == 1
    assert summary.normal_member_count == 1
    assert summary.leader is not None
    assert summary.leader.user == leader_user

    with CaptureQueriesContext(connection) as captured:
        display_names = [guild_member.user.manor.display_name for guild_member in summary.members]

    manor_queries = [q for q in captured.captured_queries if 'from "gameplay_manor"' in q["sql"].lower()]
    assert len(manor_queries) == 0
    assert len(display_names) == 3


@pytest.mark.django_db
def test_load_pending_applications_materializes_once():
    leader_user = _create_user("guild_apps_leader")
    applicant_a = _create_user("guild_apps_a")
    applicant_b = _create_user("guild_apps_b")

    guild = Guild.objects.create(name="申请帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    GuildApplication.objects.create(guild=guild, applicant=applicant_a, message="A", status="pending")
    GuildApplication.objects.create(guild=guild, applicant=applicant_b, message="B", status="pending")

    with CaptureQueriesContext(connection) as captured:
        applications = load_pending_applications(guild)

    application_queries = [q for q in captured.captured_queries if 'from "guild_applications"' in q["sql"].lower()]
    assert len(application_queries) == 1
    assert [app.applicant.username for app in applications] == ["guild_apps_b", "guild_apps_a"]


@pytest.mark.django_db
def test_load_recent_announcements_preloads_author_manor():
    leader_user = _create_user("guild_notice_leader")
    guild = Guild.objects.create(name="公告帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    GuildAnnouncement.objects.create(guild=guild, type="leader", content="公告一", author=leader_user)
    GuildAnnouncement.objects.create(guild=guild, type="system", content="公告二", author=leader_user)

    with CaptureQueriesContext(connection) as captured:
        announcements = load_recent_announcements(guild)

    announcement_queries = [q for q in captured.captured_queries if 'from "guild_announcements"' in q["sql"].lower()]
    assert len(announcement_queries) == 1
    assert len(announcements) == 2

    with CaptureQueriesContext(connection) as captured:
        author_names = [announcement.author.manor.display_name for announcement in announcements if announcement.author]

    manor_queries = [q for q in captured.captured_queries if 'from "gameplay_manor"' in q["sql"].lower()]
    assert len(manor_queries) == 0
    assert author_names == [leader_user.manor.display_name, leader_user.manor.display_name]


@pytest.mark.django_db
def test_load_guild_leader_preloads_user_manor():
    leader_user = _create_user("guild_leader_query_user")
    guild = Guild.objects.create(name="帮主查询帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")

    with CaptureQueriesContext(connection) as captured:
        leader = load_guild_leader(guild)

    guild_member_queries = [q for q in captured.captured_queries if 'from "guild_members"' in q["sql"].lower()]
    assert len(guild_member_queries) == 1
    assert leader is not None

    with CaptureQueriesContext(connection) as captured:
        display_name = leader.user.manor.display_name

    manor_queries = [q for q in captured.captured_queries if 'from "gameplay_manor"' in q["sql"].lower()]
    assert len(manor_queries) == 0
    assert display_name == leader_user.manor.display_name


@pytest.mark.django_db
def test_get_reviewable_application_preloads_applicant():
    leader_user = _create_user("guild_review_leader")
    applicant = _create_user("guild_review_applicant")
    guild = Guild.objects.create(name="审批查询帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    application = GuildApplication.objects.create(guild=guild, applicant=applicant, message="hello", status="pending")

    with CaptureQueriesContext(connection) as captured:
        loaded = get_reviewable_application(guild, application.id)

    application_queries = [q for q in captured.captured_queries if 'from "guild_applications"' in q["sql"].lower()]
    assert len(application_queries) == 1

    with CaptureQueriesContext(connection) as captured:
        username = loaded.applicant.username

    user_queries = [q for q in captured.captured_queries if 'from "auth_user"' in q["sql"].lower()]
    assert len(user_queries) == 0
    assert username == applicant.username


@pytest.mark.django_db
def test_get_manageable_member_preloads_user():
    leader_user = _create_user("guild_manage_leader")
    target_user = _create_user("guild_manage_target")
    guild = Guild.objects.create(name="成员查询帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    target_member = GuildMember.objects.create(guild=guild, user=target_user, position="member")

    with CaptureQueriesContext(connection) as captured:
        loaded = get_manageable_member(guild.id, target_member.id)

    guild_member_queries = [q for q in captured.captured_queries if 'from "guild_members"' in q["sql"].lower()]
    assert len(guild_member_queries) == 1

    with CaptureQueriesContext(connection) as captured:
        username = loaded.user.username

    user_queries = [q for q in captured.captured_queries if 'from "auth_user"' in q["sql"].lower()]
    assert len(user_queries) == 0
    assert username == target_user.username


@pytest.mark.django_db
def test_load_ordered_technologies_returns_sorted_list():
    leader_user = _create_user("guild_tech_leader")
    guild = Guild.objects.create(name="科技排序帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    GuildTechnology.objects.create(guild=guild, tech_key="march_speed", category="welfare", level=1)
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", category="combat", level=1, max_level=5)
    GuildTechnology.objects.create(guild=guild, tech_key="legacy_key", category="combat", level=1)

    technologies = load_ordered_technologies(guild)

    assert [(tech.category, tech.tech_key) for tech in technologies] == [
        ("combat", "troop_tactics"),
        ("welfare", "march_speed"),
    ]


@pytest.mark.django_db
def test_load_ordered_technologies_excludes_name_only_unsupported_rows(monkeypatch):
    from guilds import constants as guild_constants

    leader_user = _create_user("guild_tech_supported_keys_leader")
    guild = Guild.objects.create(name="科技支持判定帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    GuildTechnology.objects.create(guild=guild, tech_key="display_only", category="combat", level=1)
    GuildTechnology.objects.create(guild=guild, tech_key="march_speed", category="welfare", level=1)

    monkeypatch.setitem(guild_constants.TECH_NAMES, "display_only", "仅展示科技")
    monkeypatch.delitem(guild_constants.TECH_UPGRADE_COSTS, "display_only", raising=False)

    technologies = load_ordered_technologies(guild)

    assert [tech.tech_key for tech in technologies] == ["march_speed"]


@pytest.mark.django_db
def test_load_donation_logs_preloads_member_manor():
    leader_user = _create_user("guild_donate_leader")
    donor_user = _create_user("guild_donate_user")
    guild = Guild.objects.create(name="捐赠日志帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    donor_member = GuildMember.objects.create(guild=guild, user=donor_user, position="member")
    GuildDonationLog.objects.create(
        guild=guild,
        member=donor_member,
        resource_type="silver",
        amount=10,
        contribution_gained=10,
    )

    with CaptureQueriesContext(connection) as captured:
        logs = load_donation_logs(guild)

    donation_queries = [q for q in captured.captured_queries if 'from "guild_donation_logs"' in q["sql"].lower()]
    assert len(donation_queries) == 1

    with CaptureQueriesContext(connection) as captured:
        names = [log.member.user.manor.display_name for log in logs]

    manor_queries = [q for q in captured.captured_queries if 'from "gameplay_manor"' in q["sql"].lower()]
    assert len(manor_queries) == 0
    assert names == [donor_user.manor.display_name]


@pytest.mark.django_db
def test_load_resource_logs_preloads_related_user():
    leader_user = _create_user("guild_resource_leader")
    actor_user = _create_user("guild_resource_actor")
    guild = Guild.objects.create(name="资源日志帮", founder=leader_user, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader_user, position="leader")
    GuildResourceLog.objects.create(guild=guild, action="donation", silver_change=1, related_user=actor_user)

    with CaptureQueriesContext(connection) as captured:
        logs = load_resource_logs(guild)

    resource_queries = [q for q in captured.captured_queries if 'from "guild_resource_logs"' in q["sql"].lower()]
    assert len(resource_queries) == 1

    with CaptureQueriesContext(connection) as captured:
        usernames = [log.related_user.username for log in logs if log.related_user]

    user_queries = [q for q in captured.captured_queries if 'from "auth_user"' in q["sql"].lower()]
    assert len(user_queries) == 0
    assert usernames == [actor_user.username]
