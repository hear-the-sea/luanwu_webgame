"""精简版同一集结占城、仓库订单与受限军需调剂的实际入口回归。"""

from uuid import uuid4

import pytest

from core.exceptions import GameError
from gameplay.models import SeasonExpedition, SeasonRally
from gameplay.selectors.season import get_season_context
from gameplay.services.season.actions import perform_action
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.warfare import campaign_nodes
from tests.test_season_resource_integration import advance_resource_run, finish_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def simple_world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    season.rules["campaign"]["simplification"]["enabled"] = True
    season.save(update_fields=["rules"])
    return season, player, clock


def test_one_siege_rally_clears_guards_breaks_wall_and_captures_without_reassembly(simple_world):
    season, player, clock = simple_world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 1000
    state.save(update_fields=["troops"])
    config = season.rules["campaign"]["sites"]["mountain:gate"]
    config["control_required"] = 25
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.guards, site.durability = 1, 25
    site.save(update_fields=["guards", "durability"])
    result = perform_action(
        member.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {"node_key": site.key, "mode": "siege", "guest_ids": [guest.pk], "troops": {"dao_sheng": 100}},
    )
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    assert rally.result["unified_siege"]
    clock[0] = rally.gathers_until
    assert process_expedition(run.pk, clock[0])
    for _ in range(8):
        rally.refresh_from_db()
        if rally.status == "resolved":
            break
        clock[0] = rally.due_at
        assert process_expedition(run.pk, clock[0])
    site.refresh_from_db()
    run.refresh_from_db()
    assert site.owner == member.faction
    assert season.rallies.count() == 1
    assert run.status == "returning" and run.battles == 1
    assert any(line["kind"] == "demolition" for line in run.result["operation_costs"]["expenses"])


def test_redundant_new_orders_rejected_but_old_dispatched_raid_still_settles(simple_world):
    season, player, clock = simple_world
    member, guest = player()
    for mode, node in (("npc_intercept", "river:docks"), ("pve", "forest:granary")):
        with pytest.raises(GameError, match="不支持"):
            issue(member, guest, clock, mode=mode, node=node)
    with pytest.raises(GameError):
        perform_action(
            member.manor,
            season.pk,
            "rally_create",
            str(uuid4()),
            {"node_key": "mountain:gate", "mode": "attack", "guest_ids": [guest.pk], "troops": {}},
        )
    season.rules["campaign"]["simplification"]["enabled"] = False
    season.save(update_fields=["rules"])
    raid = issue(member, guest, clock, mode="npc_intercept", node="river:docks")
    season.rules["campaign"]["simplification"]["enabled"] = True
    season.save(update_fields=["rules"])
    advance_resource_run(raid, clock)
    assert raid.report_id and raid.status == "returning"


def test_warehouse_dispatches_real_supply_and_it_is_interceptable_at_docks(simple_world):
    from gameplay.services.season.activities import hostile_transports

    season, player, clock = simple_world
    member, guest = player()
    enemy, _ = player("history")
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.durability -= 100
    site.save(update_fields=["owner", "durability"])
    run = issue(member, guest, clock, mode="transport", node="river:warehouse", target_site_key=site.key)
    advance_resource_run(run, clock)
    assert run.status == "transporting"
    assert hostile_transports(enemy, "river:docks", clock[0]).filter(pk=run.pk).exists()
    finish_resource_run(run, clock)
    site.refresh_from_db()
    assert site.supplies["wood"] == 120 and site.supplies["iron"] == 60


def test_market_real_shortage_loss_shared_limit_and_token_retry(simple_world):
    season, player, clock = simple_world
    member, _ = player()
    state = season.faction_states.get(faction=member.faction)
    state.resources = {"grain": 100000, "wood": 0, "iron": 0}
    state.save(update_fields=["resources"])
    before_merit = member.merit
    payload = {"source_resource": "grain", "target_resource": "iron"}
    token = str(uuid4())
    first = perform_action(member.manor, season.pk, "market_exchange", token, payload)
    assert perform_action(member.manor, season.pk, "market_exchange", token, payload) == first
    assert first["cost"] == 200 and first["received"] == 53
    for _ in range(4):
        perform_action(member.manor, season.pk, "market_exchange", str(uuid4()), payload)
    with pytest.raises(GameError, match="额度"):
        perform_action(member.manor, season.pk, "market_exchange", str(uuid4()), payload)
    state.refresh_from_db()
    member.refresh_from_db()
    assert state.resources == {"grain": 99000, "wood": 0, "iron": 265}
    assert member.merit == before_merit
    assert member.actions.filter(kind="market_exchange").count() == 5
    ally, _ = player()
    with pytest.raises(GameError, match="额度"):
        perform_action(ally.manor, season.pk, "market_exchange", str(uuid4()), payload)


def test_market_rejects_non_surplus_and_foreign_resource_keys(simple_world):
    season, player, _ = simple_world
    member, _ = player()
    for source, target in (("grain", "wood"), ("gold", "iron"), ("grain", "grain")):
        with pytest.raises(GameError):
            perform_action(
                member.manor,
                season.pk,
                "market_exchange",
                str(uuid4()),
                {"source_resource": source, "target_resource": target},
            )


def test_map_roles_and_warehouse_orders_are_read_only(simple_world):
    season, player, clock = simple_world
    member, _ = player()
    before = list(season.faction_states.values("resources", "troops", "produced_at"))
    nodes = campaign_nodes(season)
    assert "attack" not in nodes["mountain:gate"]["modes"]
    assert "siege" in nodes["mountain:gate"]["modes"]
    assert "npc_intercept" not in nodes["river:docks"]["modes"]
    assert "pve" not in nodes["forest:granary"]["modes"]
    assert nodes["river:warehouse"]["kind"] == "logistics"
    context = get_season_context(member.manor, season.pk)
    rendered = {node["key"]: node for region in context["season_maps"] for node in region["nodes"]}
    assert rendered["mountain:gate"]["rally_modes"] == [{"key": "siege", "name": "集结攻城"}]
    assert "transport" not in [m["key"] for m in rendered["river:docks"]["personal_modes"]]
    assert context["simplification"]["enabled"]
    # 城防完好无需让玩家先派维修再收到错误。
    assert "repair" not in [m["key"] for m in rendered["mountain:gate"]["personal_modes"]]
    assert list(season.faction_states.values("resources", "troops", "produced_at")) == before


def test_new_garrison_carries_real_first_hour_food_without_creating_resources(simple_world):
    season, player, clock = simple_world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 100
    state.save(update_fields=["troops"])
    before = state.resources["grain"]
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    run = issue(member, guest, clock, mode="garrison", node=site.key, troops={"dao_sheng": 100})
    reserved = run.result["operation_costs"]["march_balance"]
    assert run.result["operation_costs"]["carried_food_balance"] == 8
    from gameplay.services.season.logistics import site_demand

    assert site_demand(season, site, clock[0])["grain"] == 0
    state.refresh_from_db()
    assert state.resources["grain"] == before - reserved - 8
    advance_resource_run(run, clock)
    site.refresh_from_db()
    assert site.supplies["grain"] == 8
    assert run.result["operation_costs"]["carried_food_balance"] == 0
    assert run.status == "stationed"


def test_cancelled_garrison_returns_undelivered_carried_food(simple_world):
    season, player, clock = simple_world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 100
    state.save(update_fields=["troops"])
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    before = state.resources["grain"]
    run = issue(member, guest, clock, mode="garrison", node=site.key, troops={"dao_sheng": 100})
    perform_action(member.manor, season.pk, "recall", str(uuid4()), {"run_id": run.pk})
    finish_resource_run(run, clock)
    state.refresh_from_db()
    site.refresh_from_db()
    assert site.supplies == {}
    assert state.resources["grain"] == before - run.result["operation_costs"]["march_spent"]
    assert run.result["operation_costs"]["carried_food_balance"] == 0


def test_old_attack_rally_keeps_clear_only_behavior_after_upgrade(simple_world):
    season, player, clock = simple_world
    member, guest = player()
    season.rules["campaign"]["simplification"]["enabled"] = False
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.guards = 0
    site.save(update_fields=["guards"])
    result = perform_action(
        member.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {"node_key": site.key, "mode": "attack", "guest_ids": [guest.pk], "troops": {}},
    )
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    season.rules["campaign"]["simplification"]["enabled"] = True
    season.save(update_fields=["rules"])
    clock[0] = rally.gathers_until
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    clock[0] = run.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    site.refresh_from_db()
    assert run.status == "returning" and site.owner == ""
    assert "另行发起破城" in run.result["message"]


def test_existing_docks_transport_keeps_delivering_after_warehouse_becomes_entry(simple_world):
    season, player, clock = simple_world
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.durability -= 100
    site.save(update_fields=["owner", "durability"])
    season.rules["campaign"]["simplification"]["enabled"] = False
    season.save(update_fields=["rules"])
    run = issue(member, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    season.rules["campaign"]["simplification"]["enabled"] = True
    season.save(update_fields=["rules"])
    finish_resource_run(run, clock)
    site.refresh_from_db()
    assert site.supplies["wood"] == 120 and site.supplies["iron"] == 60
