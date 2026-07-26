from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from gameplay.models import ItemTemplate
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from guilds import constants as guild_constants
from guilds.models import GuildRaidRun, GuildTroopStorage, GuildWarehouse
from guilds.services import guild_raid_rules
from tests.guild_pvp_service.support import create_guest, create_guild_with_leader, create_template


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_reserves_loot_until_the_winner_returns(
    django_user_model,
    monkeypatch,
):
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "进攻帮")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "防守帮")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_finalize_tpl"),
        name="主攻门客",
    )
    now = timezone.now()
    attacker_guild.silver = 0
    attacker_guild.save(update_fields=["silver"])
    defender_guild.silver = 100000
    defender_guild.save(update_fields=["silver"])
    GuildWarehouse.objects.create(guild=defender_guild, item_key="grain", quantity=10, contribution_cost=2)
    GuildWarehouse.objects.create(guild=defender_guild, item_key="gold_bar", quantity=5, contribution_cost=50)
    GuildWarehouse.objects.create(guild=defender_guild, item_key="red_ruby", quantity=5, contribution_cost=0)
    GuildWarehouse.objects.create(guild=defender_guild, item_key="guild_badge", quantity=9, contribution_cost=1)
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        attacker_troop_tech_snapshot={"archer_attack": 4, "archer_hp": 2},
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
    )

    monkeypatch.setattr(
        "guilds.services.guild_raids.execute_battle",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.send_guild_raid_report_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.schedule_guild_raid_completion",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raid_loot.get_guild_raid_rules",
        lambda: {
            "silver_floor": 20000,
            "silver_loot_percent": 10,
            "warehouse_loot_percent": 20,
            "warehouse_loot_whitelist": ["grain", "gold_bar", "red_ruby"],
        },
    )
    monkeypatch.setattr("gameplay.services.pvp_runtime.loot.random.randrange", lambda _stop: 0)

    from guilds.services.guild_raids import finalize_guild_raid, process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True

    attacker_guild.refresh_from_db()
    defender_guild.refresh_from_db()
    run.refresh_from_db()

    assert attacker_guild.silver == 0
    assert defender_guild.silver == 92000
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="gold_bar").exists() is False
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="grain").exists() is False
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="red_ruby").exists() is False
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="guild_badge").exists() is False
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="grain").quantity == 10
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="gold_bar").quantity == 1
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="red_ruby").quantity == 5
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="guild_badge").quantity == 9
    assert run.status == GuildRaidRun.Status.RETURNING
    assert run.is_attacker_victory is True
    assert run.loot_silver == 8000
    assert run.loot_items == {"gold_bar": 4}
    assert run.loot_item_contribution_costs == {"gold_bar": 50}
    assert run.loot_settled is False
    assert run.battle_report_id == report.id
    assert run.completed_at is None

    assert finalize_guild_raid(run, now=run.return_at) is True

    attacker_guild.refresh_from_db()
    run.refresh_from_db()
    attacker_gold = GuildWarehouse.objects.get(guild=attacker_guild, item_key="gold_bar")
    assert attacker_guild.silver == 8000
    assert attacker_gold.quantity == 4
    assert attacker_gold.contribution_cost == 50
    assert attacker_gold.total_produced == 0
    assert run.status == GuildRaidRun.Status.COMPLETED
    assert run.loot_settled is True


@pytest.mark.django_db(transaction=True)
def test_reserve_guild_raid_loot_clamps_twenty_percent_draw_by_item_capacity(
    django_user_model,
    monkeypatch,
):
    attacker_guild, _attacker_member, _attacker_manor = create_guild_with_leader(django_user_model, "运力进攻帮")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "运力防守帮")
    item_key = "guild_capacity_loot_item"
    ItemTemplate.objects.create(
        key=item_key,
        name="帮会重型战利品",
        tradeable=True,
        storage_space=1000,
    )
    defender_item = GuildWarehouse.objects.create(
        guild=defender_guild,
        item_key=item_key,
        quantity=100,
        contribution_cost=17,
        total_produced=120,
        total_exchanged=20,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raid_loot.get_guild_raid_rules",
        lambda: {
            "silver_floor": 20000,
            "silver_loot_percent": 10,
            "warehouse_loot_percent": 20,
            "warehouse_loot_whitelist": [item_key],
        },
    )
    monkeypatch.setattr("gameplay.services.pvp_runtime.loot.random.randrange", lambda _stop: 0)

    from guilds.services.guild_raid_loot import reserve_guild_raid_loot

    with transaction.atomic():
        loot_silver, loot_items, loot_item_costs = reserve_guild_raid_loot(
            attacker_guild=attacker_guild,
            defender_guild=defender_guild,
            guests=[SimpleNamespace()],
            troop_loadout={},
            battle_report=None,
        )

    assert loot_silver == 0
    assert loot_items == {item_key: 2}
    assert loot_item_costs == {item_key: 17}
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key=item_key).exists() is False
    defender_item.refresh_from_db()
    assert defender_item.quantity == 98
    assert defender_item.total_produced == 120
    assert defender_item.total_exchanged == 20


@pytest.mark.django_db(transaction=True)
def test_reserve_guild_raid_loot_uses_fractional_grain_capacity(
    django_user_model,
    monkeypatch,
):
    attacker_guild, _attacker_member, _attacker_manor = create_guild_with_leader(django_user_model, "粮食运力进攻帮")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "粮食运力防守帮")
    ItemTemplate.objects.create(
        key="grain",
        name="粮食",
        tradeable=True,
        storage_space=1,
    )
    GuildWarehouse.objects.create(guild=defender_guild, item_key="grain", quantity=10_000)
    monkeypatch.setattr(
        "guilds.services.guild_raid_loot.get_guild_raid_rules",
        lambda: {
            "silver_floor": 20_000,
            "silver_loot_percent": 10,
            "warehouse_loot_percent": 20,
            "warehouse_loot_whitelist": ["grain"],
        },
    )
    monkeypatch.setattr(
        "guilds.services.guild_raid_loot._calculate_guild_item_loot_capacity",
        lambda **_kwargs: 1,
    )

    from guilds.services.guild_raid_loot import reserve_guild_raid_loot

    with transaction.atomic():
        loot_silver, loot_items, loot_item_costs = reserve_guild_raid_loot(
            attacker_guild=attacker_guild,
            defender_guild=defender_guild,
            guests=[],
            troop_loadout={},
            battle_report=None,
        )

    assert loot_silver == 0
    assert loot_items == {"grain": 1_000}
    assert loot_item_costs == {"grain": 0}
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="grain").exists() is False
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="grain").quantity == 9_000


@pytest.mark.django_db(transaction=True)
def test_reserve_guild_raid_loot_includes_technology_production_items(
    django_user_model,
    monkeypatch,
):
    attacker_guild, _attacker_member, _attacker_manor = create_guild_with_leader(django_user_model, "科技库存进攻帮")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "科技库存防守帮")
    production_item_key = "guild_dynamic_tech_box"
    unrelated_item_key = "guild_unrelated_stock"
    ItemTemplate.objects.create(key=production_item_key, name="科技兑换箱", storage_space=1)
    GuildWarehouse.objects.create(
        guild=defender_guild,
        item_key=production_item_key,
        quantity=1,
        contribution_cost=100,
    )
    GuildWarehouse.objects.create(
        guild=defender_guild,
        item_key=unrelated_item_key,
        quantity=1,
        contribution_cost=100,
    )
    monkeypatch.setattr(guild_constants, "GUILD_PVP_SILVER_LOOT_PERCENT", 0)
    monkeypatch.setattr(guild_constants, "GUILD_PVP_WAREHOUSE_LOOT_PERCENT", 100)
    monkeypatch.setattr(
        guild_constants,
        "GUILD_PVP_WAREHOUSE_LOOT_WHITELIST",
        ["grain", "gold_bar", "red_ruby"],
    )
    monkeypatch.setattr(
        guild_raid_rules,
        "get_warehouse_production_item_keys",
        lambda: frozenset({production_item_key}),
    )

    from guilds.services.guild_raid_loot import reserve_guild_raid_loot

    with transaction.atomic():
        _loot_silver, loot_items, loot_item_costs = reserve_guild_raid_loot(
            attacker_guild=attacker_guild,
            defender_guild=defender_guild,
            guests=[SimpleNamespace()],
            troop_loadout={},
            battle_report=None,
        )

    assert loot_items == {production_item_key: 1}
    assert loot_item_costs == {production_item_key: 100}
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key=production_item_key).exists() is False
    assert not GuildWarehouse.objects.filter(guild=defender_guild, item_key=production_item_key).exists()
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key=unrelated_item_key).quantity == 1


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_grants_salvage_to_defender_warehouse_on_defense_success(
    django_user_model,
    monkeypatch,
):
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "进攻败北")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "守成")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_defense_salvage_tpl"),
        name="攻城门客",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        attacker_troop_tech_snapshot={"archer_attack": 4, "archer_hp": 2},
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={},
        drops={},
        winner="defender",
        starts_at=now,
        completed_at=now,
    )

    monkeypatch.setattr("guilds.services.guild_raids.execute_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        "guilds.services.guild_raids.calculate_battle_salvage",
        lambda *_args, **_kwargs: (3, {"iron_sword": 2}),
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.send_guild_raid_report_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.schedule_guild_raid_completion",
        lambda *_args, **_kwargs: None,
    )

    from guilds.services.guild_raids import process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True

    run.refresh_from_db()
    assert run.status == GuildRaidRun.Status.RETURNING
    assert run.is_attacker_victory is False
    assert run.battle_rewards == {"experience_fruit": 3, "iron_sword": 2}
    experience_fruit = GuildWarehouse.objects.get(guild=defender_guild, item_key="experience_fruit")
    recovered_equipment = GuildWarehouse.objects.get(guild=defender_guild, item_key="iron_sword")
    assert experience_fruit.quantity == 3
    assert experience_fruit.contribution_cost == 0
    assert experience_fruit.total_produced == 0
    assert recovered_equipment.quantity == 2
    assert recovered_equipment.contribution_cost == 0
    assert recovered_equipment.total_produced == 0


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_uses_and_applies_defender_troops(django_user_model, monkeypatch):
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "进攻测防")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "守方护院")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_defender_troops_tpl"),
        name="主攻门客",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        attacker_troop_tech_snapshot={"archer_attack": 4, "archer_hp": 2},
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )

    defender_troop_template = TroopTemplate.objects.create(key="guild_defense_archer", name="守方弓手")
    GuildTroopStorage.objects.create(guild=defender_guild, troop_template=defender_troop_template, count=500)
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={"guild_defense_archer": 500},
        rounds=[],
        losses={
            "attacker": {"casualties": []},
            "defender": {"casualties": [{"key": "guild_defense_archer", "lost": 3}]},
        },
        drops={},
        winner="defender",
        starts_at=now,
        completed_at=now,
    )

    captured: dict[str, object] = {}

    def _fake_execute_battle(_manor, _guests, _active_guests, options):
        captured["defender_setup"] = dict(options.defender_setup or {})
        captured["attacker_tech_levels"] = dict(options.attacker_tech_levels or {})
        return report

    monkeypatch.setattr("guilds.services.guild_raids.execute_battle", _fake_execute_battle)
    monkeypatch.setattr(
        "guilds.services.guild_raids.build_guild_troop_tech_levels",
        lambda guild: (
            {"archer_attack": 4, "archer_hp": 2}
            if guild.pk == attacker_guild.pk
            else {"archer_attack": 1, "archer_hp": 1}
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "guilds.services.guild_troops.build_guild_troop_tech_levels",
        lambda guild: (
            {"archer_attack": 4, "archer_hp": 2}
            if guild.pk == attacker_guild.pk
            else {"archer_attack": 1, "archer_hp": 1}
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.send_guild_raid_report_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.schedule_guild_raid_completion",
        lambda *_args, **_kwargs: None,
    )

    from guilds.services.guild_raids import process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True

    defender_storage = GuildTroopStorage.objects.get(guild=defender_guild, troop_template=defender_troop_template)
    assert captured["attacker_tech_levels"] == {"archer_attack": 4, "archer_hp": 2}
    assert captured["defender_setup"] == {
        "troop_loadout": {"guild_defense_archer": 500},
        "technology": {"levels": {"archer_attack": 1, "archer_hp": 1}},
    }
    assert defender_storage.count == 497


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_attacker_troops_use_guild_tech_levels_not_manor_tech(
    django_user_model,
    monkeypatch,
):
    from battle.troops import invalidate_troop_templates_cache
    from gameplay.models import PlayerTechnology

    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "攻方科技归属")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "守方科技归属")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_attacker_guild_tech_tpl"),
        name="帮会科技门客",
    )
    TroopTemplate.objects.update_or_create(
        key="archer",
        defaults={
            "name": "弓手",
            "base_attack": 10,
            "base_defense": 4,
            "base_hp": 20,
            "speed_bonus": 5,
            "priority": 1,
            "default_count": 0,
        },
    )
    invalidate_troop_templates_cache()
    archer_template = TroopTemplate.objects.get(key="archer")
    GuildTroopStorage.objects.create(guild=attacker_guild, troop_template=archer_template, count=10)
    PlayerTechnology.objects.create(manor=attacker_manor, tech_key="gong_attack", level=10)
    PlayerTechnology.objects.create(manor=attacker_manor, tech_key="gong_hp", level=10)

    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={"archer": 3},
        attacker_troop_tech_snapshot={"gong_attack": 2, "gong_hp": 1},
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )

    captured: dict[str, object] = {}

    def _fake_execute_simulation(attacker_units, defender_units, options, config, rng, final_seed):
        troop = next(unit for unit in attacker_units if getattr(unit, "template_key", "") == "archer")
        captured["unit_attack"] = troop.unit_attack
        captured["unit_hp"] = troop.unit_hp
        captured["tech_effects"] = dict(troop.tech_effects)
        simulation = SimpleNamespace(
            losses={"attacker": {"casualties": []}, "defender": {"casualties": []}},
            drops={},
            winner="defender",
            rounds=[],
            starts_at=timezone.now(),
            completed_at=timezone.now(),
            seed=final_seed,
        )
        return simulation, options.opponent_name or config.get("name", "帮会战")

    monkeypatch.setattr(
        "guilds.services.guild_raids.build_guild_troop_tech_levels",
        lambda _guild: {"gong_attack": 2, "gong_hp": 1},
        raising=False,
    )
    monkeypatch.setattr(
        "guilds.services.guild_troops.build_guild_troop_tech_levels",
        lambda _guild: {"gong_attack": 2, "gong_hp": 1},
        raising=False,
    )
    monkeypatch.setattr("battle.execution._execute_simulation", _fake_execute_simulation)
    monkeypatch.setattr("guilds.services.guild_raids.calculate_battle_salvage", lambda *_args, **_kwargs: (0, {}))
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_report_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda *_args, **_kwargs: None)

    from guilds.services.guild_raids import process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True
    assert captured["unit_attack"] == 12
    assert captured["unit_hp"] == 22
    assert captured["tech_effects"] == {}


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_falls_back_to_current_guild_tech_for_legacy_empty_snapshot(
    django_user_model,
    monkeypatch,
):
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "攻方兼容")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "守方兼容")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_legacy_snapshot_tpl"),
        name="兼容门客",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        attacker_troop_tech_snapshot={},
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={},
        drops={},
        winner="defender",
        starts_at=now,
        completed_at=now,
    )

    captured = {}

    def _fake_execute_battle(*args, **kwargs):
        captured["options"] = args[3]
        return report

    monkeypatch.setattr("guilds.services.guild_raids.execute_battle", _fake_execute_battle)
    monkeypatch.setattr(
        "guilds.services.guild_raids.build_guild_troop_tech_levels",
        lambda _guild: {"archer_attack": 4, "archer_hp": 2},
        raising=False,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.send_guild_raid_report_messages",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.schedule_guild_raid_completion",
        lambda *_args, **_kwargs: None,
    )

    from guilds.services.guild_raids import process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True
    assert captured["options"].attacker_tech_levels == {"archer_attack": 4, "archer_hp": 2}


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_uses_launch_time_troop_tech_snapshot_after_guild_upgrade(
    django_user_model,
    monkeypatch,
):
    from battle.troops import invalidate_troop_templates_cache
    from gameplay.models import PlayerTechnology
    from guilds.models import GuildTechnology
    from tests.guild_pvp_service.support import seed_attacker_lineup

    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "攻方时序")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "守方时序")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_snapshot_timing_tpl"),
        name="时序门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=attacker_member, guest=attacker_guest)
    troop_tactics = GuildTechnology.objects.create(
        guild=attacker_guild,
        tech_key="troop_tactics",
        category="combat",
        level=2,
        max_level=10,
    )
    TroopTemplate.objects.update_or_create(
        key="archer",
        defaults={
            "name": "弓手",
            "base_attack": 10,
            "base_defense": 4,
            "base_hp": 20,
            "speed_bonus": 5,
            "priority": 1,
            "default_count": 0,
        },
    )
    invalidate_troop_templates_cache()
    archer_template = TroopTemplate.objects.get(key="archer")
    GuildTroopStorage.objects.create(guild=attacker_guild, troop_template=archer_template, count=10)
    PlayerTechnology.objects.create(manor=attacker_manor, tech_key="gong_attack", level=10)
    PlayerTechnology.objects.create(manor=attacker_manor, tech_key="gong_hp", level=10)

    monkeypatch.setattr("guilds.services.guild_raids.calculate_guild_raid_travel_time", lambda *_a, **_k: 120)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda _run: None)
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_warning_messages", lambda *_a, **_k: None)

    from guilds.services.guild_raids import process_guild_raid_battle, start_guild_raid

    run = start_guild_raid(
        guild=attacker_guild,
        defender_guild=defender_guild,
        operator=attacker_member.user,
        pool_entry_ids=[pool_entry_id],
        troop_loadout={"archer": 3},
    )
    troop_tactics.level = 10
    troop_tactics.save(update_fields=["level"])

    captured: dict[str, object] = {}

    def _fake_execute_simulation(attacker_units, defender_units, options, config, rng, final_seed):
        troop = next(unit for unit in attacker_units if getattr(unit, "template_key", "") == "archer")
        captured["unit_attack"] = troop.unit_attack
        captured["unit_hp"] = troop.unit_hp
        simulation = SimpleNamespace(
            losses={"attacker": {"casualties": []}, "defender": {"casualties": []}},
            drops={},
            winner="defender",
            rounds=[],
            starts_at=timezone.now(),
            completed_at=timezone.now(),
            seed=final_seed,
        )
        return simulation, options.opponent_name or config.get("name", "帮会战")

    monkeypatch.setattr("battle.execution._execute_simulation", _fake_execute_simulation)
    monkeypatch.setattr("guilds.services.guild_raids.calculate_battle_salvage", lambda *_a, **_k: (0, {}))
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_report_messages", lambda *_a, **_k: None)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda *_a, **_k: None)

    assert process_guild_raid_battle(run, now=run.battle_at) is True
    assert captured["unit_attack"] == 12
    assert captured["unit_hp"] == 24


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_defers_report_messages_until_after_commit(django_user_model, monkeypatch):
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "战报延后攻方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "战报延后守方")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_report_after_commit_tpl"),
        name="战报延后门客",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
    )

    callbacks: list[object] = []
    report_message_run_ids: list[int] = []

    monkeypatch.setattr(
        "guilds.services.guild_raids.transaction.on_commit", lambda callback: callbacks.append(callback)
    )
    monkeypatch.setattr("guilds.services.guild_raids.execute_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr("guilds.services.guild_raids.calculate_battle_salvage", lambda *_args, **_kwargs: (0, {}))
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "guilds.services.guild_raids.send_guild_raid_report_messages",
        lambda current_run, _report: report_message_run_ids.append(current_run.id),
    )

    from guilds.services.guild_raids import process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True
    assert report_message_run_ids == []
    assert len(callbacks) == 1

    callbacks[0]()

    assert report_message_run_ids == [run.id]


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_raid_marks_returning_run_completed_and_returns_surviving_troops(
    django_user_model,
):
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "返程帮")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "终点帮")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_return_tpl"),
        name="返程门客",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={"guild_return_archer": 10},
        travel_time=300,
        battle_at=now - timedelta(seconds=300),
        return_at=now,
        is_attacker_victory=True,
        loot_silver=321,
        loot_items={"guild_return_reward": 2},
        loot_item_contribution_costs={"guild_return_reward": 45},
        loot_settled=False,
    )
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={"guild_return_archer": 10},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={
            "attacker": {"casualties": [{"key": "guild_return_archer", "lost": 4}]},
            "defender": {"casualties": []},
        },
        drops={},
        winner="attacker",
        starts_at=now - timedelta(seconds=300),
        completed_at=now - timedelta(seconds=300),
    )
    run.battle_report = report
    run.save(update_fields=["battle_report"])

    troop_template = TroopTemplate.objects.create(key="guild_return_archer", name="返程弓手")
    GuildTroopStorage.objects.create(guild=attacker_guild, troop_template=troop_template, count=0)

    from guilds.services.guild_raids import finalize_guild_raid

    assert finalize_guild_raid(run, now=now) is True

    run.refresh_from_db()
    attacker_guild.refresh_from_db()
    storage = GuildTroopStorage.objects.get(guild=attacker_guild, troop_template=troop_template)
    loot_item = GuildWarehouse.objects.get(guild=attacker_guild, item_key="guild_return_reward")
    assert run.status == GuildRaidRun.Status.COMPLETED
    assert run.completed_at == now
    assert run.loot_settled is True
    assert storage.count == 6
    assert attacker_guild.silver == 321
    assert loot_item.quantity == 2
    assert loot_item.contribution_cost == 45
    assert loot_item.total_produced == 0

    assert finalize_guild_raid(run, now=now) is False
    attacker_guild.refresh_from_db()
    loot_item.refresh_from_db()
    assert attacker_guild.silver == 321
    assert loot_item.quantity == 2


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_raid_preserves_existing_warehouse_contribution_cost(django_user_model):
    attacker_guild, attacker_member, _attacker_manor = create_guild_with_leader(
        django_user_model,
        "已有库存返程帮",
    )
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(
        django_user_model,
        "已有库存终点帮",
    )
    existing_item = GuildWarehouse.objects.create(
        guild=attacker_guild,
        item_key="existing_guild_return_reward",
        quantity=7,
        contribution_cost=12,
        total_produced=100,
        total_exchanged=20,
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.RETURNING,
        troop_loadout={},
        return_at=now,
        is_attacker_victory=True,
        loot_items={"existing_guild_return_reward": 2},
        loot_item_contribution_costs={"existing_guild_return_reward": 45},
        loot_settled=False,
    )

    from guilds.services.guild_raids import finalize_guild_raid

    assert finalize_guild_raid(run, now=now) is True

    existing_item.refresh_from_db()
    run.refresh_from_db()
    assert existing_item.quantity == 9
    assert existing_item.contribution_cost == 12
    assert existing_item.total_produced == 100
    assert existing_item.total_exchanged == 20
    assert run.loot_settled is True


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_raid_does_not_duplicate_historical_already_settled_loot(django_user_model):
    attacker_guild, attacker_member, _attacker_manor = create_guild_with_leader(
        django_user_model,
        "历史返程帮",
    )
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(
        django_user_model,
        "历史终点帮",
    )
    attacker_guild.silver = 500
    attacker_guild.save(update_fields=["silver"])
    historical_item = GuildWarehouse.objects.create(
        guild=attacker_guild,
        item_key="historical_guild_loot",
        quantity=2,
        contribution_cost=30,
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.RETURNING,
        troop_loadout={},
        return_at=now,
        is_attacker_victory=True,
        loot_silver=500,
        loot_items={"historical_guild_loot": 2},
        loot_item_contribution_costs={},
        loot_settled=True,
    )

    from guilds.services.guild_raids import finalize_guild_raid

    assert finalize_guild_raid(run, now=now) is True

    attacker_guild.refresh_from_db()
    historical_item.refresh_from_db()
    run.refresh_from_db()
    assert attacker_guild.silver == 500
    assert historical_item.quantity == 2
    assert run.status == GuildRaidRun.Status.COMPLETED
    assert run.loot_settled is True
