from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from gameplay.models import PlayerTroop
from gameplay.services.manor.core import ensure_manor
from guilds.models import (
    Guild,
    GuildCavalryTraining,
    GuildMember,
    GuildMissionRun,
    GuildMissionTemplate,
    GuildRaidRun,
    GuildTechnology,
    GuildTroopStorage,
    GuildWarehouse,
)
from guilds.services.assets import get_guild_troop_assets
from guilds.services.cavalry import get_guild_cavalry_recruitment_state

pytestmark = pytest.mark.django_db


@pytest.fixture
def asset_world(django_user_model):
    user = django_user_model.objects.create_user(username="guild_assets_leader")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="资产详情帮", founder=user, silver=3000, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    opponent_user = django_user_model.objects.create_user(username="guild_assets_opponent")
    opponent = Guild.objects.create(name="其他帮会", founder=opponent_user, is_active=True)
    dao, _ = TroopTemplate.objects.get_or_create(key="dao_ke", defaults={"name": "刀客"})
    qiang, _ = TroopTemplate.objects.get_or_create(key="qiang_ke", defaults={"name": "枪客"})
    return guild, member, manor, opponent, dao, qiang


def test_asset_counts_combine_stationed_and_deployed_without_personal_troops(asset_world, django_assert_num_queries):
    guild, member, manor, opponent, dao, qiang = asset_world
    GuildTroopStorage.objects.create(guild=guild, troop_template=dao, count=100)
    GuildTroopStorage.objects.create(guild=guild, troop_template=qiang, count=50)
    GuildTroopStorage.objects.create(guild=opponent, troop_template=dao, count=999)
    PlayerTroop.objects.create(manor=manor, troop_template=dao, count=500)
    GuildWarehouse.objects.create(guild=guild, item_key="bloodthirsty_cavalry", quantity=20, contribution_cost=3)
    GuildCavalryTraining.objects.create(
        guild=guild, operator=member.user, quantity=10, complete_at=timezone.now() + timedelta(minutes=5)
    )
    template = GuildMissionTemplate.objects.create(key="assets_mission", name="资产出征")
    GuildMissionRun.objects.create(guild=guild, template=template, troop_loadout={"dao_ke": 7})
    GuildRaidRun.objects.create(attacker_guild=guild, defender_guild=opponent, troop_loadout={"dao_ke": 4})
    now = timezone.now()
    report = BattleReport.objects.create(
        manor=manor,
        opponent_name="对手",
        winner="attacker",
        starts_at=now,
        completed_at=now,
        losses={"attacker": {"casualties": [{"key": "qiang_ke", "lost": 3}]}},
    )
    returning = GuildRaidRun.objects.create(
        attacker_guild=guild,
        defender_guild=opponent,
        troop_loadout={"qiang_ke": 10},
        status=GuildRaidRun.Status.RETURNING,
        battle_report=report,
    )
    GuildRaidRun.objects.create(
        attacker_guild=guild,
        defender_guild=opponent,
        troop_loadout={"dao_ke": 6},
        status=GuildRaidRun.Status.RETREATED,
    )
    GuildRaidRun.objects.create(
        attacker_guild=guild,
        defender_guild=opponent,
        troop_loadout={"dao_ke": 300},
        status=GuildRaidRun.Status.COMPLETED,
    )
    GuildRaidRun.objects.create(attacker_guild=opponent, defender_guild=guild, troop_loadout={"dao_ke": 300})
    cavalry = get_guild_cavalry_recruitment_state(guild, member)
    with django_assert_num_queries(5, exact=False):
        data = get_guild_troop_assets(guild, cavalry=cavalry)
    assert (data["stationed"], data["deployed"], data["redeemable"], data["training"], data["total"]) == (
        150,
        24,
        20,
        10,
        194,
    )
    rows = {row["key"]: row for row in data["rows"]}
    assert rows["dao_ke"]["total"] == 117
    assert rows["qiang_ke"]["deployed"] == 7
    assert rows["bloodthirsty_cavalry"]["total"] == 20
    assert rows["bloodthirsty_cavalry"]["training"] == 10
    returning.refresh_from_db()
    assert returning.status == GuildRaidRun.Status.RETURNING


def test_assets_empty_state_ignores_zero_stock(asset_world, client):
    guild, member, _manor, _opponent, dao, _qiang = asset_world
    GuildTroopStorage.objects.create(guild=guild, troop_template=dao, count=0)
    client.force_login(member.user)
    response = client.get(reverse("guilds:warehouse"))
    assert response.status_code == 200
    assert response.context["troop_assets"]["total"] == 0
    assert response.context["troop_assets"]["rows"] == []
    body = response.content.decode()
    assert "帮会资产" in body and "护院详情" in body and "物品与道具" in body
    assert "暂无帮会护院" in body
    assert "帮会仓库" not in body


def test_assets_settle_training_before_counting_and_keep_totals_on_all_item_pages(asset_world, client):
    guild, member, _manor, _opponent, dao, _qiang = asset_world
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_bloodthirsty_cavalry", level=1, max_level=1)
    GuildTroopStorage.objects.create(guild=guild, troop_template=dao, count=7)
    GuildCavalryTraining.objects.create(
        guild=guild, operator=member.user, quantity=5, complete_at=timezone.now() - timedelta(seconds=1)
    )
    GuildWarehouse.objects.bulk_create(
        [
            GuildWarehouse(guild=guild, item_key=f"assets_item_{index}", quantity=1, contribution_cost=1)
            for index in range(55)
        ]
    )
    client.force_login(member.user)
    first = client.get(reverse("guilds:warehouse"))
    second = client.get(reverse("guilds:warehouse"), {"page": 2})
    for response in (first, second):
        assert response.status_code == 200
        data = response.context["troop_assets"]
        assert (data["total"], data["stationed"], data["redeemable"], data["training"]) == (12, 7, 5, 0)
    assert GuildWarehouse.objects.get(guild=guild, item_key="bloodthirsty_cavalry").quantity == 5


def test_only_current_guild_members_can_view_assets(asset_world, client):
    _guild, _member, _manor, opponent, _dao, _qiang = asset_world
    client.force_login(opponent.founder)
    response = client.get(reverse("guilds:warehouse"))
    assert response.status_code == 302
    assert response.url == reverse("guilds:hall")
