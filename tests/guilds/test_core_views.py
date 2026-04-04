from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild


@pytest.fixture
def guild_guest_client(django_user_model):
    user = django_user_model.objects.create_user(username="guild_guest_view_user", password="pass12345")
    ensure_manor(user)

    client = Client()
    assert client.login(username="guild_guest_view_user", password="pass12345")
    return client


def _create_active_guilds(django_user_model, *, prefix: str, count: int) -> list[Guild]:
    guilds: list[Guild] = []
    for index in range(count):
        founder = django_user_model.objects.create_user(username=f"{prefix}_founder_{index}", password="pass12345")
        ensure_manor(founder)
        guilds.append(Guild.objects.create(name=f"{prefix}帮会{index}", founder=founder, is_active=True))
    return guilds


@pytest.mark.django_db
def test_guild_hall_reads_latest_runtime_display_limit(monkeypatch, guild_guest_client, django_user_model):
    _create_active_guilds(django_user_model, prefix="guild_hall_runtime_limit", count=3)
    monkeypatch.setattr("guilds.constants.GUILD_HALL_DISPLAY_LIMIT", 1)

    response = guild_guest_client.get(reverse("guilds:hall"))

    assert response.status_code == 200
    assert len(response.context["guilds"]) == 1


@pytest.mark.django_db
def test_guild_list_reads_latest_runtime_page_size(monkeypatch, guild_guest_client, django_user_model):
    _create_active_guilds(django_user_model, prefix="guild_list_runtime_limit", count=3)
    monkeypatch.setattr("guilds.constants.GUILD_LIST_PAGE_SIZE", 1)

    response = guild_guest_client.get(reverse("guilds:list"))

    assert response.status_code == 200
    assert len(response.context["guilds"]) == 1


@pytest.mark.django_db
def test_create_guild_page_reads_latest_runtime_creation_cost(monkeypatch, guild_guest_client):
    monkeypatch.setattr("guilds.constants.GUILD_CREATION_COST", {"gold_bar": 11})

    response = guild_guest_client.get(reverse("guilds:create"))

    assert response.status_code == 200
    assert response.context["creation_cost"] == {"gold_bar": 11}
