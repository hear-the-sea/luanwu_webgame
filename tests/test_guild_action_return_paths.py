from __future__ import annotations

import pytest
from django.urls import reverse

from core.exceptions import GameError, GuildWarehouseError
from guilds.models import Guild, GuildMissionRun, GuildMissionTemplate, GuildRaidRun
from tests.guild_mission_views.support import guild_member_client as _guild_member_client

guild_member_client = _guild_member_client


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["mission", "pvp"])
@pytest.mark.parametrize("rejected", [False, True])
def test_guild_retreat_returns_to_home_for_home_actions(guild_member_client, monkeypatch, kind, rejected):
    client, user, guild = guild_member_client
    if kind == "mission":
        template = GuildMissionTemplate.objects.create(key="return_home", name="撤回测试")
        run = GuildMissionRun.objects.create(guild=guild, template=template)
        service = "guilds.views.missions.guild_mission_service.request_retreat"
    else:
        target = Guild.objects.create(name="返回地址测试目标", founder=user)
        run = GuildRaidRun.objects.create(attacker_guild=guild, defender_guild=target)
        service = "guilds.views.pvp.guild_raid_service.request_retreat"

    def retreat(**_kwargs):
        if rejected:
            raise GameError("当前无法撤回")

    monkeypatch.setattr(service, retreat)
    response = client.post(reverse(f"guilds:{kind}_retreat"), {"run_id": run.pk, "next": reverse("home")})
    assert response.status_code == 302
    assert response.url == reverse("home")


@pytest.mark.django_db
@pytest.mark.parametrize("kind,default_view", [("mission", "missions"), ("pvp", "pvp")])
@pytest.mark.parametrize("next_url", ["", "https://untrusted.example/exit"])
def test_guild_retreat_invalid_input_uses_safe_return_path(guild_member_client, kind, default_view, next_url):
    client, _user, _guild = guild_member_client
    response = client.post(reverse(f"guilds:{kind}_retreat"), {"run_id": "invalid", "next": next_url})
    assert response.status_code == 302
    assert response.url == reverse(f"guilds:{default_view}")


@pytest.mark.django_db
@pytest.mark.parametrize("rejected", [False, True])
def test_guild_exchange_retains_current_page(guild_member_client, monkeypatch, rejected):
    client, _user, _guild = guild_member_client

    def exchange(*_args):
        if rejected:
            raise GuildWarehouseError("贡献不足")

    monkeypatch.setattr("guilds.views.warehouse.warehouse_service.exchange_item", exchange)
    response = client.post(reverse("guilds:exchange_item", args=["experience_fruit"]), {"quantity": 1, "page": 3})
    assert response.status_code == 302
    assert response.url == f"{reverse('guilds:warehouse')}?page=3"
