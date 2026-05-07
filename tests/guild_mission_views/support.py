from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember


def create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def create_template_with_avatar(key: str, *, name: str | None = None) -> GuestTemplate:
    resolved_name = name or f"模板{key}"
    return GuestTemplate.objects.create(
        key=key,
        name=resolved_name,
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        avatar=build_uploaded_gif(f"{key}.gif"),
    )


def build_uploaded_gif(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name=name,
        content=(
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
            b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x00"
            b"\x00\x00\x00\x00,\x00\x00\x00\x00\x01"
            b"\x00\x01\x00\x00\x02\x02D\x01\x00;"
        ),
        content_type="image/gif",
    )


def create_guest(*, manor, template: GuestTemplate, name: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=20,
        force=120,
        intellect=85,
        defense_stat=100,
        agility=90,
        luck=60,
    )


@pytest.fixture
def guild_member_client(django_user_model):
    user, _manor = create_user_with_manor(django_user_model, "guild_mission_view_leader")
    guild = Guild.objects.create(name="帮会任务视图帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)

    client = Client()
    assert client.login(username="guild_mission_view_leader", password="pass12345")
    return client, user, guild
