from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildAnnouncement, GuildMember


@pytest.fixture
def guild_announcement_client(django_user_model):
    user = django_user_model.objects.create_user(username="announcement_account", password="pass12345")
    manor = ensure_manor(user)
    manor.name = "松风庄园"
    manor.save(update_fields=["name"])
    guild = Guild.objects.create(name="松风公告帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)

    base_time = timezone.now() - timedelta(hours=1)
    for index in range(7):
        announcement = GuildAnnouncement.objects.create(
            guild=guild,
            type="leader",
            content=f"第{index + 1}条公告",
            author=user,
        )
        GuildAnnouncement.objects.filter(pk=announcement.pk).update(created_at=base_time + timedelta(minutes=index))

    client = Client()
    assert client.login(username=user.username, password="pass12345")
    return client


@pytest.mark.django_db
def test_announcement_list_uses_manor_name_and_paginates_three_per_page(guild_announcement_client):
    first_page_response = guild_announcement_client.get(reverse("guilds:announcements"))

    assert first_page_response.status_code == 200
    first_page = first_page_response.context["announcement_page"]
    assert first_page.paginator.per_page == 3
    assert first_page.paginator.count == 7
    assert first_page.paginator.num_pages == 3
    assert [announcement.content for announcement in first_page_response.context["announcements"]] == [
        "第7条公告",
        "第6条公告",
        "第5条公告",
    ]
    body = first_page_response.content.decode("utf-8")
    assert '<strong class="tw-guild-announcement-author">松风庄园</strong>' in body

    last_page_response = guild_announcement_client.get(reverse("guilds:announcements") + "?page=3")

    assert last_page_response.status_code == 200
    assert [announcement.content for announcement in last_page_response.context["announcements"]] == ["第1条公告"]


@pytest.mark.django_db
def test_guild_detail_shows_only_latest_announcement(guild_announcement_client):
    guild = Guild.objects.get(name="松风公告帮")

    response = guild_announcement_client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    assert [announcement.content for announcement in response.context["announcements"]] == ["第7条公告"]
    assert response.content.decode("utf-8").count("tw-guild-announcement ") == 1
    assert "第6条公告" not in response.content.decode("utf-8")
