"""帮会产出与成员变化遵守庄园优先锁序，并在并发入帮时安全回滚。"""

import threading

import pytest
from django.db import close_old_connections, connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.exceptions import GuildMembershipError
from gameplay.models import Manor
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildApplication, GuildMember, GuildTechnology, GuildWarehouse
from guilds.services import contribution
from guilds.services import guild as guild_service
from guilds.services import member as member_service
from guilds.services import technology
from guilds.services.resource_locks import lock_guild_resource_manors, settle_guild_resource_manors


@pytest.fixture
def guild_resource_world(django_user_model):
    leader = django_user_model.objects.create_user(username="resource_lock_leader")
    player = django_user_model.objects.create_user(username="resource_lock_member")
    for user in (leader, player):
        manor = ensure_manor(user)
        Manor.objects.filter(pk=manor.pk).update(silver=100000, silver_capacity=1000000)
    guild = Guild.objects.create(name="资源锁序帮会", founder=leader, silver=100000000, active_technology_route="sheep")
    GuildMember.objects.create(guild=guild, user=leader, position="leader")
    member = GuildMember.objects.create(guild=guild, user=player, position="member")
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_farmland", category="sheep", level=1, max_level=3)
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=10000000)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=10000)
    return guild, leader, member


def _change_guild(kind, guild, leader, member):
    if kind == "leave":
        member_service._leave_guild_state(member)
    elif kind == "kick":
        member_service._kick_member_state(member, leader)
    elif kind == "disband":
        guild_service.disband_guild(guild, leader)
    elif kind == "upgrade":
        technology.upgrade_technology(guild, "sheep_farmland", leader)
    else:
        technology.reset_technology_route(guild, "sheep", leader)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["leave", "kick", "disband", "upgrade", "reset"])
def test_resource_changes_acquire_manors_before_guild(kind, guild_resource_world, monkeypatch):
    guild, leader, member = guild_resource_world
    targets = []
    original = QuerySet._fetch_all

    def capture(queryset):
        if queryset.query.select_for_update and queryset._result_cache is None:
            targets.append(queryset.model)
        return original(queryset)

    monkeypatch.setattr(QuerySet, "_fetch_all", capture)
    _change_guild(kind, guild, leader, member)
    assert targets.index(Manor) < targets.index(Guild)


@pytest.mark.django_db
def test_new_member_after_manor_discovery_aborts_instead_of_locking_in_reverse(guild_resource_world, django_user_model):
    guild, _leader, _member = guild_resource_world
    newcomer = django_user_model.objects.create_user(username="resource_lock_newcomer")
    ensure_manor(newcomer)
    with pytest.raises(GuildMembershipError, match="成员已变化"):
        with transaction.atomic():
            scope = lock_guild_resource_manors(guild.pk)
            locked_guild = Guild.objects.select_for_update().get(pk=guild.pk)
            GuildMember.objects.create(guild=guild, user=newcomer)
            settle_guild_resource_manors(locked_guild, scope, now=timezone.now())
    assert not GuildMember.objects.filter(user=newcomer).exists()
    guild.refresh_from_db()
    assert guild.is_active


@pytest.mark.django_db
def test_member_who_left_after_discovery_is_not_settled_again(guild_resource_world, monkeypatch):
    guild, leader, member = guild_resource_world
    settled = []
    monkeypatch.setattr(
        "guilds.services.resource_locks.settle_resource_production_locked",
        lambda manor, **_kwargs: settled.append(manor.user_id),
    )
    with transaction.atomic():
        scope = lock_guild_resource_manors(guild.pk)
        locked_guild = Guild.objects.select_for_update().get(pk=guild.pk)
        GuildMember.objects.filter(pk=member.pk).update(is_active=False)
        assert settle_guild_resource_manors(locked_guild, scope, now=timezone.now()) == [leader.pk]
    assert settled == [leader.pk]


@pytest.mark.django_db
def test_auto_approval_cannot_join_a_disbanded_guild(guild_resource_world, django_user_model):
    guild, _leader, _member = guild_resource_world
    applicant = django_user_model.objects.create_user(username="resource_lock_old_application")
    ensure_manor(applicant)
    application = GuildApplication.objects.create(guild=guild, applicant=applicant)
    Guild.objects.filter(pk=guild.pk).update(is_active=False)
    with pytest.raises(GuildMembershipError, match="已解散"):
        member_service._approve_application_state(application, None, auto=True)
    application.refresh_from_db()
    assert application.status == "pending"
    assert not GuildMember.objects.filter(user=applicant).exists()


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["leave", "kick", "disband", "upgrade", "reset"])
def test_donation_can_finish_when_guild_change_waits_for_its_manor(kind, guild_resource_world):
    if connection.vendor != "mysql":
        pytest.skip("requires MySQL row locking")
    guild, leader, member = guild_resource_world
    donor_has_manor = threading.Event()
    changer_attempted_manor = threading.Event()
    errors = []

    def worker(donating):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
            manor_query_seen = False

            def coordinate(execute, sql, params, many, context):
                nonlocal manor_query_seen
                is_manor_lock = "for update" in sql.lower() and "gameplay_manor" in sql.lower()
                if is_manor_lock and not manor_query_seen:
                    manor_query_seen = True
                    if not donating:
                        changer_attempted_manor.set()
                    result = execute(sql, params, many, context)
                    if donating:
                        donor_has_manor.set()
                        assert changer_attempted_manor.wait(5), "guild change did not reach its manor lock"
                    return result
                return execute(sql, params, many, context)

            with connection.execute_wrapper(coordinate):
                if donating:
                    contribution.donate_resource(GuildMember.objects.get(pk=member.pk), "silver", 1000)
                else:
                    assert donor_has_manor.wait(5), "donation did not acquire its manor"
                    _change_guild(kind, Guild.objects.get(pk=guild.pk), leader, GuildMember.objects.get(pk=member.pk))
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(donating,)) for donating in (True, False)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    member.refresh_from_db()
    assert member.total_contribution == 1
