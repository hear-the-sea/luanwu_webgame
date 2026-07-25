from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

import guilds.constants as guild_constants
from battle.models import TroopTemplate
from core.exceptions import GuildValidationError
from gameplay.models import PlayerTroop
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildTroopDonationLog, GuildTroopStorage


@pytest.fixture
def guild_member_with_troops(django_user_model):
    user = django_user_model.objects.create_user(username="gt_donor", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="护院捐赠帮会", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)

    troop_template = TroopTemplate.objects.create(key="donation_archer", name="捐赠弓兵")
    player_troop = PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=10)
    return guild, member, troop_template, player_troop


@pytest.mark.django_db
def test_donate_troops_success_moves_from_player_to_guild_and_logs(guild_member_with_troops):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    contribution_gained = guild_troop_service.donate_troops(
        member=member,
        troop_key=troop_template.key,
        quantity=4,
    )

    player_troop.refresh_from_db()
    member.refresh_from_db()
    assert player_troop.count == 6
    assert contribution_gained == 4
    assert member.current_contribution == 4
    assert member.weekly_contribution == 4
    assert member.total_contribution == 4

    storage = GuildTroopStorage.objects.get(guild=member.guild, troop_template=troop_template)
    assert storage.count == 4

    assert GuildTroopDonationLog.objects.filter(
        guild=member.guild,
        member=member,
        troop_template=troop_template,
        quantity=4,
    ).exists()


def test_troop_donation_rates_follow_recruitment_technology_tiers():
    from guilds.services import guild_troops as guild_troop_service

    assert guild_constants.DAILY_TROOP_CONTRIBUTION_LIMIT == 300
    assert guild_troop_service.get_troop_donation_rate("scout") == 1
    assert guild_troop_service.get_troop_donation_rate("dao_ke") == 1
    assert guild_troop_service.get_troop_donation_rate("dao_jie") == 3
    assert guild_troop_service.get_troop_donation_rate("dao_ba") == 6
    assert guild_troop_service.get_troop_donation_rate("dao_sheng") == 12
    assert guild_troop_service.get_troop_donation_rate("custom_unknown_guard") == 1


@pytest.mark.django_db
def test_donate_troops_rejects_batch_exceeding_daily_contribution_limit(
    guild_member_with_troops,
    monkeypatch,
):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    monkeypatch.setattr("guilds.constants.DAILY_TROOP_CONTRIBUTION_LIMIT", 5)
    guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=4)

    with pytest.raises(GuildValidationError, match="今日护院捐赠贡献已达上限"):
        guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=2)

    player_troop.refresh_from_db()
    member.refresh_from_db()
    storage = GuildTroopStorage.objects.get(guild=member.guild, troop_template=troop_template)
    assert player_troop.count == 6
    assert storage.count == 4
    assert member.current_contribution == 4
    assert member.weekly_contribution == 4
    assert member.total_contribution == 4
    assert GuildTroopDonationLog.objects.filter(member=member).count() == 1


@pytest.mark.django_db
def test_yesterdays_troop_donation_does_not_use_todays_limit(guild_member_with_troops, monkeypatch):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    monkeypatch.setattr("guilds.constants.DAILY_TROOP_CONTRIBUTION_LIMIT", 5)
    guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=4)
    GuildTroopDonationLog.objects.filter(member=member).update(donated_at=timezone.now() - timezone.timedelta(days=1))

    assert guild_troop_service.get_today_troop_contribution(member) == 0
    gained = guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=5)

    player_troop.refresh_from_db()
    member.refresh_from_db()
    assert gained == 5
    assert player_troop.count == 1
    assert member.current_contribution == 9
    assert member.weekly_contribution == 9
    assert member.total_contribution == 9
    assert guild_troop_service.get_today_troop_contribution(member) == 5


@pytest.mark.django_db
def test_donate_troops_rejects_contribution_overflow(guild_member_with_troops):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import contribution as contribution_service
    from guilds.services import guild_troops as guild_troop_service

    GuildMember.objects.filter(pk=member.pk).update(
        current_contribution=contribution_service.MAX_CONTRIBUTION,
        weekly_contribution=contribution_service.MAX_CONTRIBUTION,
        total_contribution=contribution_service.MAX_CONTRIBUTION,
    )

    with pytest.raises(GuildValidationError, match="贡献度已达上限"):
        guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=1)

    player_troop.refresh_from_db()
    assert player_troop.count == 10
    assert not GuildTroopStorage.objects.filter(guild=member.guild, troop_template=troop_template).exists()
    assert not GuildTroopDonationLog.objects.filter(member=member).exists()


@pytest.mark.django_db
def test_donate_troops_rejects_non_positive_quantity(guild_member_with_troops):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    with pytest.raises(GuildValidationError):
        guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=0)

    with pytest.raises(GuildValidationError):
        guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=-1)

    player_troop.refresh_from_db()
    assert player_troop.count == 10
    assert not GuildTroopStorage.objects.filter(guild=member.guild, troop_template=troop_template).exists()
    assert not GuildTroopDonationLog.objects.filter(guild=member.guild, troop_template=troop_template).exists()


@pytest.mark.django_db
def test_donate_troops_rejects_missing_troop_key(guild_member_with_troops):
    _guild, member, _troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    with pytest.raises(GuildValidationError):
        guild_troop_service.donate_troops(member=member, troop_key="not_exist", quantity=1)

    player_troop.refresh_from_db()
    assert player_troop.count == 10


@pytest.mark.django_db
def test_donate_troops_rejects_insufficient_quantity(guild_member_with_troops):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    with pytest.raises(GuildValidationError):
        guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=999)

    player_troop.refresh_from_db()
    assert player_troop.count == 10
    assert not GuildTroopStorage.objects.filter(guild=member.guild, troop_template=troop_template).exists()
    assert not GuildTroopDonationLog.objects.filter(guild=member.guild, troop_template=troop_template).exists()


@pytest.mark.django_db
def test_donate_troops_updates_player_and_guild_storage_timestamps(guild_member_with_troops):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    old_time = timezone.now() - timezone.timedelta(days=1)
    PlayerTroop.objects.filter(pk=player_troop.pk).update(updated_at=old_time)
    GuildTroopStorage.objects.create(guild=member.guild, troop_template=troop_template, count=1, updated_at=old_time)

    guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=3)

    player_troop.refresh_from_db()
    storage = GuildTroopStorage.objects.get(guild=member.guild, troop_template=troop_template)
    assert player_troop.updated_at > old_time
    assert storage.updated_at > old_time
    assert storage.count == 4


@pytest.mark.django_db
def test_donate_troops_recovers_when_storage_create_hits_integrity_error(guild_member_with_troops, monkeypatch):
    _guild, member, troop_template, player_troop = guild_member_with_troops

    from guilds.services import guild_troops as guild_troop_service

    storage = GuildTroopStorage.objects.create(guild=member.guild, troop_template=troop_template, count=2)

    class _LockedStorageSelector:
        def filter(self, **_kwargs):
            return self

        def first(self):
            return None

        def get(self, **_kwargs):
            return storage

    monkeypatch.setattr(
        "guilds.services.guild_troops.GuildTroopStorage.objects.select_for_update", lambda: _LockedStorageSelector()
    )
    monkeypatch.setattr(
        "guilds.services.guild_troops.GuildTroopStorage.objects.create",
        lambda **_kwargs: (_ for _ in ()).throw(IntegrityError("duplicate key")),
    )

    guild_troop_service.donate_troops(member=member, troop_key=troop_template.key, quantity=3)

    player_troop.refresh_from_db()
    storage.refresh_from_db()
    assert player_troop.count == 7
    assert storage.count == 5
    assert GuildTroopDonationLog.objects.filter(
        guild=member.guild,
        member=member,
        troop_template=troop_template,
        quantity=3,
    ).exists()


@pytest.mark.django_db
def test_donate_troops_view_success_redirects_back_to_guild_detail_and_sets_message(django_user_model):
    user = django_user_model.objects.create_user(username="gt_view_donor", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="护院捐赠视图帮会", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)

    troop_template = TroopTemplate.objects.create(key="donation_view_spear", name="捐赠枪兵")
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=5)

    client = Client()
    assert client.login(username="gt_view_donor", password="pass12345")

    response = client.post(
        reverse("guilds:donate_troops"),
        {"troop_key": troop_template.key, "quantity": "2"},
        follow=True,
    )

    assert response.redirect_chain
    assert response.redirect_chain[-1][0].endswith(reverse("guilds:detail", args=[guild.id]))
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert messages[-1] == "护院已捐赠到帮会护院池，获得2贡献"
    assert "护院已捐赠到帮会护院池，获得2贡献" in response.content.decode("utf-8")

    storage = GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template)
    assert storage.count == 2


@pytest.mark.django_db
def test_donate_troops_view_failure_uses_game_error_message_chain(django_user_model):
    user = django_user_model.objects.create_user(username="gt_view_fail", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="护院捐赠失败帮会", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)

    client = Client()
    assert client.login(username="gt_view_fail", password="pass12345")

    response = client.post(
        reverse("guilds:donate_troops"),
        {"troop_key": "nope", "quantity": "0"},
        follow=True,
    )

    assert response.redirect_chain
    assert response.redirect_chain[-1][0].endswith(reverse("guilds:detail", args=[guild.id]))
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert messages[-1] == "捐赠数量必须大于 0"
    assert "捐赠数量必须大于 0" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_donate_troops_view_escapes_failure_message_html(django_user_model):
    user = django_user_model.objects.create_user(username="gt_view_html", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="护院捐赠转义帮会", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)

    client = Client()
    assert client.login(username="gt_view_html", password="pass12345")

    response = client.post(
        reverse("guilds:donate_troops"),
        {"troop_key": "<b>bad</b>", "quantity": "1"},
        follow=True,
    )

    body = response.content.decode("utf-8")
    assert response.redirect_chain
    assert response.redirect_chain[-1][0].endswith(reverse("guilds:detail", args=[guild.id]))
    assert "护院数量不足" in body
    assert "&lt;b&gt;bad&lt;/b&gt;" not in body
    assert "<b>bad</b>" not in body
