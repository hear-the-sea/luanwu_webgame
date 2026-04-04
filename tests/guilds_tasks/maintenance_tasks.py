from __future__ import annotations

from datetime import timedelta

import pytest
from django.db.utils import DatabaseError
from django.utils import timezone


@pytest.mark.django_db
def test_reset_guild_weekly_stats_retries_on_error(monkeypatch):
    from guilds.tasks import reset_guild_weekly_stats

    monkeypatch.setattr("guilds.tasks.reset_weekly_contributions", lambda: (_ for _ in ()).throw(DatabaseError("x")))

    called = {"retry": 0}

    def _retry(exc):
        called["retry"] += 1
        raise RuntimeError("retried")

    monkeypatch.setattr(reset_guild_weekly_stats, "retry", _retry)

    with pytest.raises(RuntimeError, match="retried"):
        reset_guild_weekly_stats.run()

    assert called["retry"] == 1


@pytest.mark.django_db
def test_reset_guild_weekly_stats_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import reset_guild_weekly_stats

    monkeypatch.setattr(
        "guilds.tasks.reset_weekly_contributions",
        lambda: (_ for _ in ()).throw(AssertionError("broken weekly reset contract")),
    )
    monkeypatch.setattr(
        reset_guild_weekly_stats,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(AssertionError, match="broken weekly reset contract"):
        reset_guild_weekly_stats.run()


@pytest.mark.django_db
def test_cleanup_invalid_guild_hero_pool_programming_error_bubbles_up(monkeypatch):
    from guilds.tasks import cleanup_invalid_guild_hero_pool

    monkeypatch.setattr(
        "guilds.tasks.cleanup_invalid_hero_pool_entries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken hero pool cleanup contract")),
    )
    monkeypatch.setattr(
        cleanup_invalid_guild_hero_pool,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(AssertionError, match="broken hero pool cleanup contract"):
        cleanup_invalid_guild_hero_pool.run()


@pytest.mark.django_db
def test_cleanup_old_guild_logs_deletes_only_old_rows(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildDonationLog, GuildExchangeLog, GuildMember, GuildResourceLog
    from guilds.tasks import cleanup_old_guild_logs

    monkeypatch.setattr(
        cleanup_old_guild_logs,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    founder = django_user_model.objects.create_user(username="g_founder3", password="pass")
    member_user = django_user_model.objects.create_user(username="g_member", password="pass")
    guild = Guild.objects.create(name="G3", founder=founder, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=member_user)

    donation = GuildDonationLog.objects.create(
        guild=guild,
        member=member,
        resource_type="silver",
        amount=1,
        contribution_gained=1,
    )
    exchange = GuildExchangeLog.objects.create(
        guild=guild,
        member=member,
        item_key="x",
        quantity=1,
        contribution_cost=1,
    )
    resource = GuildResourceLog.objects.create(guild=guild, action="donation", silver_change=1)

    old_ts = timezone.now() - timedelta(days=31)
    GuildDonationLog.objects.filter(pk=donation.pk).update(donated_at=old_ts)
    GuildExchangeLog.objects.filter(pk=exchange.pk).update(exchanged_at=old_ts)
    GuildResourceLog.objects.filter(pk=resource.pk).update(created_at=old_ts)

    GuildDonationLog.objects.create(
        guild=guild,
        member=member,
        resource_type="silver",
        amount=2,
        contribution_gained=2,
    )

    result = cleanup_old_guild_logs.run()
    assert "cleaned up" in result
    assert GuildDonationLog.objects.count() == 1
    assert GuildExchangeLog.objects.count() == 0
    assert GuildResourceLog.objects.count() == 0


@pytest.mark.django_db
def test_cleanup_invalid_guild_hero_pool_task(monkeypatch, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
    from guilds.models import Guild, GuildBattleLineupEntry, GuildHeroPoolEntry, GuildMember
    from guilds.tasks import cleanup_invalid_guild_hero_pool

    monkeypatch.setattr(
        cleanup_invalid_guild_hero_pool,
        "retry",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    leader = django_user_model.objects.create_user(username="ghp_cleanup_leader", password="pass")
    manor = ensure_manor(leader)
    guild = Guild.objects.create(name="任务清理帮", founder=leader, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = GuestTemplate.objects.create(
        key="ghp_cleanup_tpl",
        name="清理模板",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        custom_name="清理门客",
        level=10,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=70,
        luck=50,
    )

    entry = GuildHeroPoolEntry.objects.create(
        guild=guild,
        owner_member=member,
        source_guest=guest,
        slot_index=1,
        last_submitted_at=timezone.now() - timedelta(days=8),
    )
    GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=entry, slot_index=1, selected_by=leader)

    member.is_active = False
    member.save(update_fields=["is_active"])

    result = cleanup_invalid_guild_hero_pool.run()

    assert "cleaned" in result
    assert GuildHeroPoolEntry.objects.filter(pk=entry.pk).count() == 0
    assert GuildBattleLineupEntry.objects.filter(guild=guild).count() == 0
