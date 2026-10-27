"""在内存数据库构造赛季，再通过真实 selector 渲染浏览器校验页面。"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# 必须在 Django 初始化前强制隔离；不允许继承开发或外部服务数据库。
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ["DJANGO_TEST_USE_ENV_SERVICES"] = "0"
os.environ["DJANGO_TEST_SQLITE_NAME"] = ":memory:"

import django  # noqa: E402
import pytest  # noqa: E402, F401 - 激活项目隔离测试设置。

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.db import transaction  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import resolve, reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from gameplay.models import ItemTemplate, Manor, Season, SeasonExpedition, SeasonMember, SeasonRally  # noqa: E402
from gameplay.selectors.season import get_season_context  # noqa: E402
from gameplay.services.season.campaign_rules import load_campaign_rules  # noqa: E402
from gameplay.services.season.catalog import (  # noqa: E402
    faction_template_keys,
    load_rules,
    reward_catalog,
    shop_catalog,
)
from gameplay.services.season.economy import ensure_factions_locked  # noqa: E402
from gameplay.services.season.registration import register_locked  # noqa: E402
from gameplay.services.season.warfare import ensure_sites_locked  # noqa: E402
from guests.models import Guest, GuestStatus, GuestTemplate  # noqa: E402


def render(manor: Manor, season: Season) -> str:
    request = RequestFactory().get(reverse("gameplay:season"), {"season_id": season.pk})
    request.user = manor.user
    request.resolver_match = resolve(request.path)
    context = get_season_context(manor, season.pk)
    context.update(request=request, user=manor.user, csrf_token="browser-fixture")
    html = render_to_string("gameplay/season.html", context)
    # 浏览器测试显式加载相关脚本/样式，避免聊天与在线状态连接污染校样。
    html = re.sub(r'<script\b[^>]*\bsrc="[^"]+"[^>]*></script>', "", html)
    return re.sub(r'<link\b[^>]*\brel="stylesheet"[^>]*>', "", html)


def main() -> None:
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert connection.settings_dict["NAME"] == ":memory:"
    with redirect_stdout(io.StringIO()):
        call_command("migrate", verbosity=0, interactive=False)

    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    season = Season.objects.create(
        name="浏览器校验 · 山河争衡",
        starts_at=now - timedelta(days=16),
        ends_at=now + timedelta(days=74),
        rules=rules,
    )
    user = get_user_model().objects.create_user(username="season-browser", password=None)
    manor, _ = Manor.objects.get_or_create(user=user)
    template, _ = GuestTemplate.objects.get_or_create(
        key="base_black_military", defaults={"name": "乡勇", "archetype": "military", "rarity": "black"}
    )
    template.avatar = "guests/monkey_wu_male4.png"
    template.save(update_fields=["avatar"])
    guest = Guest.objects.create(
        manor=manor, template=template, force=100, defense_stat=100, intellect=40, agility=60, current_hp=100
    )
    with transaction.atomic():
        member = register_locked(season, manor, "fiction", now)
        ensure_factions_locked(season, now)
        ensure_sites_locked(season, now)
    member.merit, member.coins, member.supplies = 3500, 1000, 12
    member.scroll_available = True
    member.save(update_fields=["merit", "coins", "supplies", "scroll_available"])

    for index, key in enumerate(sorted(faction_template_keys(member.faction, rules=rules))[:4]):
        GuestTemplate.objects.get_or_create(
            key=key,
            defaults={
                "name": f"结缘候选{index + 1}",
                "archetype": "military",
                "rarity": "blue",
                "avatar": "guests/monkey_wu_male4.png",
            },
        )
    keys = {
        key
        for row in [*reward_catalog(member.faction, rules=rules), *shop_catalog(member.faction, rules=rules)]
        for key in row.get("items", {})
    } | set(rules["summon_fallback_items"])
    keys |= {key for row in rules["campaign"]["market_control"]["pool"] for key in row["items"]}
    for key in keys:
        ItemTemplate.objects.get_or_create(key=key, defaults={"name": key})

    pages = {"open": render(manor, season)}
    from copy import deepcopy

    original_population = deepcopy(season.population)
    rules["campaign"]["resource_economy"]["enabled"] = True
    season.rules = rules
    season.population = {"factions": {member.faction: {"count": 1, "economic_count": 1}}}
    season.save(update_fields=["rules", "population"])
    pages["resources"] = render(manor, season)
    rules["campaign"]["resource_economy"]["enabled"] = False
    season.rules = rules
    season.population = original_population
    season.save(update_fields=["rules", "population"])
    ally_user = get_user_model().objects.create_user(username="season-rally-ally", password=None)
    ally_manor, _ = Manor.objects.get_or_create(user=ally_user)
    ally_manor.name = "集结盟友"
    ally_manor.save(update_fields=["name"])
    ally = SeasonMember.objects.create(season=season, manor=ally_manor, faction=member.faction)
    rally = SeasonRally.objects.create(
        season=season, leader=ally, node_key="mountain:gate", kind="attack", gathers_until=now + timedelta(minutes=30)
    )
    fixture_runs = []
    fixture_guests = []
    for owner, kind, status, name, attack in [
        (ally, "attack", "gathering", "盟友集结门客", 100),
        (member, "garrison", "stationed", "己方驻防门客", 100),
        (ally, "garrison", "stationed", "盟友驻防门客", 200),
    ]:
        unit = Guest.objects.create(manor=owner.manor, template=template, status=GuestStatus.DEPLOYED, current_hp=100)
        fixture_guests.append(unit)
        fixture_runs.append(
            SeasonExpedition.objects.create(
                member=owner,
                node_key="mountain:gate" if kind == "attack" else "river:docks",
                kind=kind,
                status=status,
                rally=rally if kind == "attack" else None,
                guest_ids=[unit.pk],
                lineup={
                    "guests": [
                        {"guest_id": unit.pk, "display_name": name, "attack": attack, "defense": 50, "current_hp": 100}
                    ]
                },
                troops={"dao_sheng": 2000},
                launched_at=now,
                due_at=now + timedelta(hours=1),
                stationed_until=now + timedelta(hours=1) if kind == "garrison" else None,
            )
        )
    docks = season.sites.get(key="river:docks")
    docks.owner = member.faction
    docks.save(update_fields=["owner"])
    pages["coalition"] = render(manor, season)
    pages["coalition_leader"] = render(ally_manor, season)
    fixture_runs[1].status = "returning"
    fixture_runs[1].save(update_fields=["status"])
    docks.owner = "history"
    docks.save(update_fields=["owner"])
    pages["returning_lost"] = render(manor, season)
    fixture_runs[1].status = "stationed"
    fixture_runs[1].save(update_fields=["status"])
    docks.owner = member.faction
    docks.save(update_fields=["owner"])
    changed_gate = season.sites.get(key="mountain:gate")
    previous_gate_owner = changed_gate.owner
    changed_gate.owner = ally.faction
    changed_gate.save(update_fields=["owner"])
    pages["rally_target_changed"] = render(ally_manor, season)
    changed_gate.owner = previous_gate_owner
    changed_gate.save(update_fields=["owner"])
    rally.status = "cancelled"
    rally.save(update_fields=["status"])
    for run in fixture_runs:
        run.status = "completed"
        run.save(update_fields=["status"])
    for unit in fixture_guests:
        unit.status = GuestStatus.IDLE
        unit.save(update_fields=["status"])
    docks.owner = ""
    docks.save(update_fields=["owner"])
    development = season.faction_states.get(faction=member.faction)
    original_resources = dict(development.resources)
    development.construction_points = 500
    development.resources = {key: 0 for key in development.resources}
    development.save(update_fields=["construction_points", "resources"])
    pages["automatic_development"] = render(manor, season)
    development.construction_points = 0
    development.resources = original_resources
    development.save(update_fields=["construction_points", "resources"])
    camp = season.sites.get(key="forest:camp")
    camp.owner = "history"
    camp.protected_until = now - timedelta(seconds=1)
    camp.save(update_fields=["owner", "protected_until"])
    stock = season.faction_states.get(faction=member.faction)
    stock.troops["bloodthirsty_cavalry"] = 35
    stock.save(update_fields=["troops"])
    pages["special_lost"] = render(manor, season)
    camp.owner, camp.protected_until = "", None
    camp.save(update_fields=["owner", "protected_until"])
    stock.troops["bloodthirsty_cavalry"] = 0
    stock.save(update_fields=["troops"])
    original_start, original_end = season.starts_at, season.ends_at
    season.starts_at, season.ends_at = now - timedelta(days=1), now + timedelta(days=89)
    season.save(update_fields=["starts_at", "ends_at"])
    member.faction = ""
    member.save(update_fields=["faction"])
    pages["registration"] = render(manor, season)
    member.faction = "fiction"
    member.save(update_fields=["faction"])
    season.starts_at, season.ends_at = original_start, original_end
    season.save(update_fields=["starts_at", "ends_at"])
    original_days = list(rules["campaign"]["expeditions"]["city_weekdays"])
    today = timezone.localtime(now).weekday()
    rules["campaign"]["expeditions"]["city_weekdays"] = [(today + 1) % 7]
    season.rules = rules
    season.save(update_fields=["rules"])
    pages["city_closed"] = render(manor, season)
    rules["campaign"]["expeditions"]["city_weekdays"] = original_days
    season.rules = rules
    season.save(update_fields=["rules"])
    # 真实公共兵库与出征接口创建队伍，校验归队前门客被占用。
    from gameplay.services.season.actions import perform_action

    perform_action(
        manor,
        season.pk,
        "dispatch",
        str(uuid4()),
        {
            "node_key": "mountain:inn",
            "mode": "pve",
            "guest_ids": [guest.pk],
            "troops": {"dao_sheng": 5},
        },
    )
    pages["deployed"] = render(manor, season)
    unregistered_user = get_user_model().objects.create_user(username="season-unregistered", password=None)
    unregistered, _ = Manor.objects.get_or_create(user=unregistered_user)
    pages["unregistered"] = render(unregistered, season)
    season.starts_at, season.ends_at = now - timedelta(days=95), now - timedelta(days=5)
    season.closed, season.winner = True, member.faction
    season.save(update_fields=["starts_at", "ends_at", "closed", "winner"])
    member.final_rank = 1
    member.final_tier = "elite"
    member.final_reward = {"items": {"experience_fruit": 40}, "tier_name": "阵营前一成", "winner_bonus": True}
    member.save(update_fields=["final_rank", "final_tier", "final_reward"])
    pages["closed"] = render(manor, season)
    # 精简版独立画面：同一集结攻城、仓库补给与真实富余/短缺调剂。
    season.starts_at, season.ends_at = now - timedelta(days=5), now + timedelta(days=85)
    season.closed, season.winner = False, ""
    rules["campaign"]["resource_economy"]["enabled"] = True
    rules["campaign"]["simplification"]["enabled"] = True
    season.rules = rules
    season.population = {"factions": {member.faction: {"count": 1, "economic_count": 1}}}
    season.save(update_fields=["starts_at", "ends_at", "closed", "winner", "rules", "population"])
    for run in SeasonExpedition.objects.all():
        run.status = "completed"
        run.save(update_fields=["status"])
    Guest.objects.create(manor=manor, template=template, current_hp=100)
    gate = season.sites.get(key="mountain:gate")
    gate.owner = member.faction
    gate.durability -= 100
    gate.supplies = {}
    gate.save(update_fields=["owner", "durability", "supplies"])
    state = season.faction_states.get(faction=member.faction)
    state.resources = {"grain": 100000, "wood": 3000, "iron": 0}
    state.save(update_fields=["resources"])
    pages["simple"] = render(manor, season)
    market = season.sites.get(key="river:market")
    market.owner = member.faction
    market.save(update_fields=["owner"])
    pages["market_owned"] = render(manor, season)
    # 一次预约与已结算周奖励用于验收新协作入口，不调用开发数据库。
    from gameplay.models import SeasonAction, SeasonWeek
    from gameplay.services.season.weekly_campaign import weekly_campaign_context

    season.starts_at, season.ends_at = now - timedelta(days=9), now + timedelta(days=81)
    season.save(update_fields=["starts_at", "ends_at"])
    state.refresh_from_db()
    state.economy_state["weekly_campaign"] = {
        "activated_at": season.starts_at.isoformat(),
        "weeks": {"1": {"target": 200, "score": 12, "victory_points": 10}},
    }
    state.save(update_fields=["economy_state"])
    SeasonWeek.objects.create(
        season=season,
        number=1,
        node_key="campaign",
        fiction_points=12,
        history_points=5,
        winner=member.faction,
        settled_at=now,
    )
    SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="fixture-weekly",
        kind="merit_credit",
        date=season.starts_at.date(),
        week=1,
        result={
            "merit": 250,
            "category": "logistics",
            "occurred_at": (season.starts_at + timedelta(days=1)).isoformat(),
        },
    )
    assert weekly_campaign_context(season, member, now)["weeks"][0]["personal_claimable"]
    pages["playable"] = render(manor, season)
    previous_lineup = deepcopy(member.lineup)
    member.lineup = {
        **member.lineup,
        "reservation": {
            "status": "pending",
            "node_key": "mountain:gate",
            "expires_at": (now + timedelta(hours=2)).isoformat(),
        },
    }
    member.save(update_fields=["lineup"])
    pages["pending_reservation_lost"] = render(manor, season)
    member.lineup = previous_lineup
    member.save(update_fields=["lineup"])
    quota_guest = Guest.objects.filter(manor=manor, status=GuestStatus.IDLE).order_by("id").last()
    for _ in range(2):
        SeasonExpedition.objects.create(
            member=member,
            node_key="mountain:inn",
            kind="pve",
            status="completed",
            guest_ids=[quota_guest.pk],
            launched_at=now,
            due_at=now,
            result={"quota_date": timezone.localdate(now).isoformat()},
        )
    pages["quota_exhausted"] = render(manor, season)
    enemy_user = get_user_model().objects.create_user(username="season-convoy-enemy", password=None)
    enemy_manor, _ = Manor.objects.get_or_create(user=enemy_user)
    enemy = SeasonMember.objects.create(season=season, manor=enemy_manor, faction="history")
    for owner in (ally, enemy):
        SeasonExpedition.objects.create(
            member=owner,
            node_key="river:warehouse",
            kind="transport",
            status="transporting",
            cargo={"grain": 6000, "wood": 1500, "iron": 1000},
            launched_at=now - timedelta(minutes=5),
            due_at=now + timedelta(minutes=40),
            result={
                "activities_version": 1,
                "activity": {"encounter_version": 1, "encounter_players": 5, "target_site_key": gate.key},
            },
        )
    pages["convoys"] = render(manor, season)
    connection.close()
    print(json.dumps(pages, ensure_ascii=False))


if __name__ == "__main__":
    main()
