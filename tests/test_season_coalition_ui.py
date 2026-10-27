from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gameplay.models import Manor, Season, SeasonExpedition, SeasonMember, SeasonRally
from gameplay.selectors.season import get_season_context
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.economy import ensure_factions_locked
from gameplay.services.season.warfare import ensure_sites_locked
from guests.models import Guest, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def coalition_ui(user_factory):
    now = timezone.now()
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    rules["campaign"]["simplification"]["enabled"] = False
    rules["campaign"].setdefault("resource_economy", {})["enabled"] = False
    season = Season.objects.create(
        name="集结与联合驻防", starts_at=now - timedelta(days=5), ends_at=now + timedelta(days=85), rules=rules
    )
    members = []
    idle_template = GuestTemplate.objects.create(
        key="season_ui_available_commander", name="待命主将", rarity="black", archetype="military"
    )
    for index, faction in enumerate(["fiction", "fiction", "history"]):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        manor.name = f"阵营驻军{index}"
        manor.save(update_fields=["name"])
        members.append(SeasonMember.objects.create(season=season, manor=manor, faction=faction))
        # 驻军快照的主将已被占用，另备一名真实待命门客才能加入进攻集结。
        Guest.objects.create(pk=9000 + index, manor=manor, template=idle_template, current_hp=100)
    with transaction.atomic():
        ensure_factions_locked(season, now)
        sites = ensure_sites_locked(season, now)

    def run(member, node, status, rally=None, troops=1000, attack=100, name="可战主将"):
        return SeasonExpedition.objects.create(
            member=member,
            node_key=node,
            kind="garrison" if status == "stationed" else "attack",
            status=status,
            rally=rally,
            guest_ids=[member.pk],
            lineup={
                "guests": [
                    {"guest_id": member.pk, "display_name": name, "attack": attack, "defense": 50, "current_hp": 100}
                ]
            },
            troops={"dao_sheng": troops},
            launched_at=now,
            due_at=now + timedelta(hours=1),
            stationed_until=now + timedelta(hours=1) if status == "stationed" else None,
        )

    viewer, ally, enemy = members
    allied = SeasonRally.objects.create(
        season=season, leader=ally, node_key="mountain:gate", kind="attack", gathers_until=now + timedelta(seconds=180)
    )
    run(ally, allied.node_key, "gathering", rally=allied)
    own = allied
    enemy_rally = SeasonRally.objects.create(
        season=season, leader=enemy, node_key="mountain:gate", kind="attack", gathers_until=now + timedelta(seconds=180)
    )
    run(enemy, enemy_rally.node_key, "gathering", rally=enemy_rally)
    front = sites["river:docks"]
    front.owner = "fiction"
    a = run(viewer, front.key, "stationed", troops=2000)
    b = run(ally, front.key, "stationed", troops=2000, attack=300, name="联合主将")
    front.front_line = {"dao_sheng": 3000}
    front.front_allocations = {str(a.pk): {"dao_sheng": 1500}, str(b.pk): {"dao_sheng": 1500}}
    front.reinforcement_at = now + timedelta(seconds=60)
    front.save()
    return season, viewer, ally, allied, own, enemy_rally


def test_rally_selector_only_shows_allies_and_joint_front_is_read_only(coalition_ui):
    season, viewer, ally, allied, own, enemy = coalition_ui
    before = list(season.sites.order_by("pk").values("front_line", "front_allocations", "reinforcement_at"))
    context = get_season_context(viewer.manor, season.pk)
    rallies = {row["id"]: row for row in context["rallies"]}
    assert set(rallies) == {allied.pk}
    assert rallies[allied.pk]["can_join"] and not rallies[allied.pk]["can_cancel"]
    owner_rally = get_season_context(ally.manor, season.pk)["rallies"][0]
    assert owner_rally["joined"] and owner_rally["can_cancel"]
    gate = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:gate")
    assert gate["rally_modes"] == [{"key": "attack", "name": "集结攻坚"}]
    assert not gate["personal_modes"]
    docks = next(row for row in context["site_rows"] if row["name"] == "渡口码头")
    defense = docks["garrison"]
    assert (defense["total_troops"], defense["member_count"], defense["guest_count"]) == (4000, 2, 2)
    assert any(
        row["name"] == ally.manor.display_name and row["guest_names"] == "联合主将" for row in defense["players"]
    )
    assert list(season.sites.order_by("pk").values("front_line", "front_allocations", "reinforcement_at")) == before


def test_rally_no_longer_advertised_joinable_when_all_guests_exhausted(coalition_ui):
    season, viewer, _ally, allied, _own, _enemy = coalition_ui
    guest = Guest.objects.get(pk=9000, manor=viewer.manor)
    now = timezone.now()
    for _ in range(season.rules["campaign"]["expeditions"]["guest_daily_limit"]):
        SeasonExpedition.objects.create(
            member=viewer,
            node_key="mountain:inn",
            kind="pve",
            status="completed",
            guest_ids=[guest.pk],
            lineup={},
            troops={},
            launched_at=now,
            due_at=now,
            result={"quota_date": timezone.localdate(now).isoformat()},
        )
    context = get_season_context(viewer.manor, season.pk)
    rally = next(row for row in context["rallies"] if row["id"] == allied.pk)
    assert not rally["can_join"]
    assert not context["joinable_rallies"]
    gate = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == allied.node_key)
    assert not gate["rallies"][0]["can_join"]
    assert "可加入" not in gate["map_hint"]
    client = Client()
    client.force_login(viewer.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert f'id="season-rally-{allied.pk}"' not in html


def test_rally_forms_and_joint_defense_render_without_cycles(coalition_ui):
    season, viewer, ally, allied, own, enemy = coalition_ui
    client = Client()
    client.force_login(viewer.manor.user)
    response = client.get(reverse("gameplay:season"), {"season_id": season.pk})
    assert response.status_code == 200
    html = response.content.decode()
    for action in ["rally_create", "rally_join"]:
        assert f'name="action" value="{action}"' in html
    assert f'id="season-rally-{allied.pk}"' in html
    assert f'data-season-rally="{enemy.pk}"' not in html
    assert "联合主将" in html
    assert "驻防队伍" in html
    assert "间隔 60 秒继续下一波" not in html
    assert "未抽中的驻军待命且不受损" not in html
    assert "最多补充 300" not in html
    client.force_login(ally.manor.user)
    owner_html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert 'name="action" value="rally_cancel"' in owner_html


def test_post_parses_rally_id_as_integer(coalition_ui, monkeypatch):
    season, viewer, ally, allied, own, enemy = coalition_ui
    captured = {}

    def perform(manor, season_id, action, token, payload):
        captured.update(action=action, payload=payload)
        return {"message": "已收到集结命令"}

    monkeypatch.setattr("gameplay.views.season.perform_action", perform)
    client = Client()
    client.force_login(viewer.manor.user)
    response = client.post(
        reverse("gameplay:season_action"),
        {"season_id": season.pk, "action": "rally_join", "action_token": str(uuid4()), "rally_id": str(allied.pk)},
    )
    assert response.status_code == 302
    assert captured["action"] == "rally_join"
    assert captured["payload"]["rally_id"] == allied.pk


def test_old_snapshot_keeps_individual_attack_ui(coalition_ui):
    season, viewer, ally, allied, own, enemy = coalition_ui
    season.rules["campaign"].pop("coalition")
    season.save(update_fields=["rules"])
    context = get_season_context(viewer.manor, season.pk)
    assert not context["coalition"]["enabled"]
    assert not context["rallies"]
    gate = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:gate")
    assert not gate["rally_modes"]
    assert {row["key"] for row in gate["personal_modes"]} == {"attack"}


def test_attack_and_garrison_slots_remain_occupied_through_return(coalition_ui):
    season, viewer, ally, allied, own, enemy = coalition_ui
    now = timezone.now()
    SeasonExpedition.objects.create(
        member=viewer,
        node_key="mountain:inn",
        kind="pve",
        status="returning",
        launched_at=now,
        due_at=now + timedelta(minutes=3),
    )
    context = get_season_context(viewer.manor, season.pk)
    assert context["attack_busy"] and context["garrison_busy"]
    assert not context["can_dispatch"]
    assert not context["rallies"][0]["can_join"]
    for region in context["season_maps"]:
        for node in region["nodes"]:
            assert not node["personal_modes"]
            assert not node["rally_modes"]


def test_daily_dispatch_display_counts_rallies_and_cancellation_refunds(coalition_ui):
    from gameplay.models import SeasonAction

    season, viewer, ally, allied, own, enemy = coalition_ui
    for kind in ("dispatch", "rally_create", "rally_join", "dispatch_refund"):
        SeasonAction.objects.create(
            member=viewer,
            token=uuid4(),
            request_hash=kind,
            kind=kind,
            date=timezone.localdate(),
            week=1,
            result={},
        )
    context = get_season_context(viewer.manor, season.pk)
    assert context["dispatches_remaining"] == context["daily_dispatch_limit"] - 2


def test_coalition_guest_selection_limit_is_three_even_for_larger_manor(coalition_ui, monkeypatch):
    season, viewer, ally, allied, own, enemy = coalition_ui
    monkeypatch.setattr(Manor, "max_squad_size", property(lambda self: 6))
    context = get_season_context(viewer.manor, season.pk)
    assert context["max_guests"] == 3
    assert context["rally_guest_limit"] == 3
