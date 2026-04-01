from __future__ import annotations

from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from core.exceptions import GuildValidationError
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember
from guilds.services import hero_pool as hero_pool_service


@pytest.fixture
def guild_member_client(django_user_model):
    user = django_user_model.objects.create_user(username="ghp_view_leader", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="门客池视图帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader")

    client = Client()
    assert client.login(username="ghp_view_leader", password="pass12345")
    return client, user, guild


def _create_guest_with_avatar(*, user, key: str, name: str) -> Guest:
    template = GuestTemplate.objects.create(
        key=key,
        name=name,
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        avatar=SimpleUploadedFile(
            name=f"{key}.gif",
            content=(
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
                b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x00"
                b"\x00\x00\x00\x00,\x00\x00\x00\x00\x01"
                b"\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        ),
    )
    manor = user.manor
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=18,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=88,
        luck=50,
    )


def _parse_hero_pool_html(response) -> BeautifulSoup:
    return BeautifulSoup(response.content.decode("utf-8"), "html.parser")


def _get_ghp_region(soup: BeautifulSoup, region_name: str):
    region = soup.select_one(f'[data-ghp-region="{region_name}"]')
    assert region is not None
    return region


def _assert_region_uses_shared_avatar_markup(region) -> None:
    avatar = region.select_one("div.guest-avatar")
    assert avatar is not None
    image = avatar.find("img")
    assert image is not None
    assert image.get("width") == "48"
    assert image.get("height") == "48"


@pytest.mark.django_db
def test_hero_pool_submit_invalid_params_show_error(guild_member_client):
    client, _user, _guild = guild_member_client

    response = client.post(reverse("guilds:hero_pool_submit"), {"slot_index": "x"}, follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert messages[-1] == "参数错误"


@pytest.mark.django_db
def test_hero_pool_submit_success_message_uses_unified_helper(guild_member_client, monkeypatch):
    client, _user, _guild = guild_member_client

    result = SimpleNamespace(
        replaced=True,
        entry=SimpleNamespace(slot_index=2),
        lineup_removed_count=1,
    )
    monkeypatch.setattr("guilds.views.hero_pool.hero_pool_service.submit_hero_pool_entry", lambda *_a, **_k: result)

    response = client.post(reverse("guilds:hero_pool_submit"), {"slot_index": 2, "guest_id": 99}, follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert messages[-1] == "已替换槽位 2 门客（原出战位已自动下阵 1 项）"


@pytest.mark.django_db
def test_lineup_add_value_error_shows_message(guild_member_client, monkeypatch):
    client, _user, _guild = guild_member_client

    def _raise(*_args, **_kwargs):
        raise GuildValidationError("出战名单已满")

    monkeypatch.setattr("guilds.views.hero_pool.hero_pool_service.add_lineup_entry", _raise)

    response = client.post(reverse("guilds:lineup_add"), {"pool_entry_id": 1}, follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert messages[-1] == "出战名单已满"


@pytest.mark.django_db
def test_hero_pool_page_uses_guest_roster_avatar_markup_for_all_sections(
    guild_member_client,
    django_user_model,
    settings,
    tmp_path,
):
    client, user, guild = guild_member_client
    settings.MEDIA_ROOT = tmp_path
    member = GuildMember.objects.get(guild=guild, user=user)

    guest = _create_guest_with_avatar(user=user, key="ghp_view_avatar_main", name="头像门客")
    pool_entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=pool_entry.id)

    other_user = django_user_model.objects.create_user(username="ghp_view_member", password="pass12345")
    ensure_manor(other_user)
    other_member = GuildMember.objects.create(guild=guild, user=other_user, position="member")
    other_guest = _create_guest_with_avatar(user=other_user, key="ghp_view_avatar_other", name="池子门客")
    hero_pool_service.submit_hero_pool_entry(other_member, guest_id=other_guest.id, slot_index=1)

    response = client.get(reverse("guilds:hero_pool"))

    assert response.status_code == 200
    soup = _parse_hero_pool_html(response)
    _assert_region_uses_shared_avatar_markup(_get_ghp_region(soup, "roster-scroll"))
    _assert_region_uses_shared_avatar_markup(_get_ghp_region(soup, "slot-summary"))
    lineup_region = _get_ghp_region(soup, "lineup-scroll")
    _assert_region_uses_shared_avatar_markup(lineup_region)
    assert f"所属玩家：{user.manor.display_name}" in lineup_region.get_text(" ", strip=True)
    body = response.content.decode("utf-8")
    assert "tw-guild-guest-avatar" not in body


@pytest.mark.django_db
def test_hero_pool_page_renders_table_sidebar_and_scroll_regions(
    guild_member_client,
    settings,
    tmp_path,
):
    client, user, guild = guild_member_client
    settings.MEDIA_ROOT = tmp_path
    member = GuildMember.objects.get(guild=guild, user=user)

    guest = _create_guest_with_avatar(user=user, key="ghp_layout_main", name="赵云")
    pool_entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=pool_entry.id)

    response = client.get(reverse("guilds:hero_pool"))

    soup = _parse_hero_pool_html(response)
    page = soup.select_one('[data-ghp-page="hero-pool"]')
    assert page is not None
    assert page.get("data-filter-count-joinable") == "0"
    assert page.get("data-filter-count-lineup") == "1"
    assert page.get("data-filter-count-mine") == "0"
    assert page.get("data-filter-count-all") == "1"
    assert _get_ghp_region(soup, "roster-scroll") is not None
    assert _get_ghp_region(soup, "slot-summary") is not None
    assert _get_ghp_region(soup, "lineup-scroll") is not None


@pytest.mark.django_db
def test_hero_pool_page_uses_name_color_without_rarity_text_labels(
    guild_member_client,
    settings,
    tmp_path,
):
    client, user, guild = guild_member_client
    settings.MEDIA_ROOT = tmp_path
    member = GuildMember.objects.get(guild=guild, user=user)

    guest = _create_guest_with_avatar(user=user, key="ghp_rarity_color", name="吕布")
    hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1)

    response = client.get(reverse("guilds:hero_pool"))

    soup = _parse_hero_pool_html(response)
    page = soup.select_one('[data-ghp-page="hero-pool"]')
    assert page is not None
    page_text = page.get_text(" ", strip=True)
    assert "稀有度" not in page_text
    assert "· 橙" not in page_text
    assert "· 紫" not in page_text
    guest_name = soup.find("span", string=guest.display_name)
    assert guest_name is not None
    assert f"rarity-text-{guest.rarity}" in (guest_name.get("class") or [])


@pytest.mark.django_db
def test_hero_pool_page_loads_dedicated_assets(guild_member_client):
    client, _user, _guild = guild_member_client

    response = client.get(reverse("guilds:hero_pool"))

    soup = _parse_hero_pool_html(response)
    assert soup.select_one('link[href$="css/guild-hero-pool.css"]') is not None
    assert soup.select_one('script[src$="js/guild-hero-pool.js"]') is not None


@pytest.mark.django_db
def test_hero_pool_page_renders_filterable_roster_rows(
    guild_member_client,
    settings,
    tmp_path,
):
    client, user, guild = guild_member_client
    settings.MEDIA_ROOT = tmp_path
    member = GuildMember.objects.get(guild=guild, user=user)

    guest = _create_guest_with_avatar(user=user, key="ghp_filter_hooks", name="张辽")
    hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1)

    response = client.get(reverse("guilds:hero_pool"))

    soup = _parse_hero_pool_html(response)
    page = soup.select_one('[data-ghp-page="hero-pool"]')
    assert page is not None
    assert page.select_one('[data-status-filter="joinable"]') is not None
    assert page.select_one('[data-status-filter="lineup"]') is not None
    assert page.select_one('[data-status-filter="mine"]') is not None
    assert page.select_one('[data-status-filter="all"]') is not None
    roster_row = page.select_one(".ghp-roster-row")
    assert roster_row is not None
    assert roster_row.get("data-status-key") == "mine"
    assert guest.display_name.lower() in (roster_row.get("data-search-text") or "")
    assert page.select_one(".ghp-search-input") is not None


@pytest.mark.django_db
def test_hero_pool_page_defaults_to_slot_one_with_switch_tabs(guild_member_client):
    client, _user, _guild = guild_member_client

    response = client.get(reverse("guilds:hero_pool"))

    soup = _parse_hero_pool_html(response)
    slot_summary = _get_ghp_region(soup, "slot-summary")
    slot_tabs = slot_summary.select("[data-ghp-slot-target]")
    assert [tab.get_text(strip=True) for tab in slot_tabs] == ["槽位1", "槽位2"]
    assert slot_tabs[0].get("aria-pressed") == "true"
    assert slot_tabs[1].get("aria-pressed") == "false"

    slot_one_card = slot_summary.select_one('[data-ghp-slot-card="1"]')
    slot_two_card = slot_summary.select_one('[data-ghp-slot-card="2"]')
    assert slot_one_card is not None
    assert slot_two_card is not None
    assert slot_one_card.get("hidden") is None
    assert slot_two_card.get("hidden") == ""


@pytest.mark.django_db
def test_hero_pool_page_keeps_lineup_remove_action_for_managers(
    guild_member_client,
    settings,
    tmp_path,
):
    client, user, guild = guild_member_client
    settings.MEDIA_ROOT = tmp_path
    member = GuildMember.objects.get(guild=guild, user=user)

    guest = _create_guest_with_avatar(user=user, key="ghp_lineup_remove", name="关羽")
    pool_entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=pool_entry.id)

    response = client.get(reverse("guilds:hero_pool"))

    soup = _parse_hero_pool_html(response)
    lineup_region = _get_ghp_region(soup, "lineup-scroll")
    remove_form = lineup_region.select_one(f'form[action$="{reverse("guilds:lineup_remove")}"]')
    assert remove_form is not None
    assert remove_form.select_one('input[name="lineup_entry_id"]') is not None
