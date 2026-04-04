from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.models import BattleReport
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember, GuildRaidRun, GuildTroopStorage, GuildWarehouse
from guilds.services import hero_pool as hero_pool_service


def _create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def _create_guild_with_leader(django_user_model, suffix: str) -> tuple[Guild, GuildMember, object]:
    leader, manor = _create_user_with_manor(django_user_model, f"guild_pvp_{suffix}")
    guild = Guild.objects.create(name=f"帮{suffix}"[:12], founder=leader, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    return guild, member, manor


def _create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def _create_guest(*, manor, template: GuestTemplate, name: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=20,
        force=120,
        intellect=80,
        defense_stat=100,
        agility=90,
        luck=60,
    )


def _seed_attacker_lineup(*, guild: Guild, leader: GuildMember, guest: Guest) -> int:
    entry = hero_pool_service.submit_hero_pool_entry(leader, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader.user, pool_entry_id=entry.id)
    return entry.id


@pytest.mark.django_db
def test_guild_raid_run_status_choices():
    statuses = {choice for choice, _label in GuildRaidRun.Status.choices}
    assert statuses == {"marching", "battling", "returning", "completed", "retreated"}


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_generates_guest_snapshots_and_travel_time(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = _create_guild_with_leader(django_user_model, "发起方")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "防守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    guest = _create_guest(
        manor=leader.user.manor,
        template=_create_template("guild_pvp_start_tpl"),
        name="进攻门客",
    )
    pool_entry_id = _seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)

    scheduled_run_ids: list[int] = []
    monkeypatch.setattr(
        "guilds.services.guild_raids.calculate_guild_raid_travel_time",
        lambda *_args, **_kwargs: 321,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.schedule_guild_raid_completion",
        lambda run: scheduled_run_ids.append(run.id),
    )

    from guilds.services.guild_raids import start_guild_raid

    run = start_guild_raid(
        guild=attacker_guild,
        defender_guild=defender_guild,
        operator=leader.user,
        pool_entry_ids=[pool_entry_id],
        troop_loadout={},
    )

    assert run.status == GuildRaidRun.Status.MARCHING
    assert run.selected_guest_count == 1
    assert run.guest_ids == [guest.id]
    assert len(run.guest_snapshots) == 1
    assert run.travel_time == 321
    assert int((run.battle_at - run.started_at).total_seconds()) == 321
    assert int((run.return_at - run.started_at).total_seconds()) == 642
    assert scheduled_run_ids == [run.id]


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_clears_attacker_defeat_protection_on_success(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = _create_guild_with_leader(django_user_model, "清保护")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "目标帮")
    attacker_guild.silver = 50000
    attacker_guild.defeat_protection_until = timezone.now() + timedelta(hours=2)
    attacker_guild.save(update_fields=["silver", "defeat_protection_until"])
    guest = _create_guest(
        manor=leader.user.manor,
        template=_create_template("guild_pvp_clear_protection_tpl"),
        name="进攻门客",
    )
    pool_entry_id = _seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)

    monkeypatch.setattr(
        "guilds.services.guild_raids.calculate_guild_raid_travel_time",
        lambda *_args, **_kwargs: 120,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.schedule_guild_raid_completion",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.send_guild_raid_warning_messages",
        lambda *_args, **_kwargs: None,
    )

    from guilds.services.guild_raids import start_guild_raid

    start_guild_raid(
        guild=attacker_guild,
        defender_guild=defender_guild,
        operator=leader.user,
        pool_entry_ids=[pool_entry_id],
        troop_loadout={},
    )

    attacker_guild.refresh_from_db()
    assert attacker_guild.defeat_protection_until is None


@pytest.mark.django_db
def test_get_guild_pvp_page_context_uses_supplied_now_for_counter_projection(django_user_model):
    guild, member, _manor = _create_guild_with_leader(django_user_model, "计数投影")
    reference_now = timezone.now() - timedelta(days=1)
    reference_today = timezone.localdate(reference_now)
    Guild.objects.filter(pk=guild.pk).update(
        pvp_attack_count_today=2,
        pvp_attack_count_reset_at=reference_today,
        pvp_defense_count_today=1,
        pvp_defense_count_reset_at=reference_today,
    )
    guild.refresh_from_db()

    from guilds.services.guild_pvp_queries import get_guild_pvp_page_context

    context = get_guild_pvp_page_context(member, now=reference_now)

    assert context["attack_count"] == 2
    assert context["defense_count"] == 1


@pytest.mark.django_db
def test_prepare_guild_pvp_read_state_processes_due_incoming_marching_run(django_user_model, monkeypatch):
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "读侧守方")
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "读侧攻方")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_read_state_tpl"),
        name="读侧门客",
    )
    now = timezone.now()
    due_run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        travel_time=300,
        battle_at=now - timedelta(seconds=5),
        return_at=now + timedelta(seconds=295),
    )
    processed_run_ids: list[int] = []
    monkeypatch.setattr(
        "guilds.services.guild_raids.process_due_guild_raid",
        lambda run, now=None: processed_run_ids.append(run.id) or True,
    )

    from guilds.services.guild_raids import prepare_guild_pvp_read_state

    prepare_guild_pvp_read_state(defender_guild, now=now)

    assert processed_run_ids == [due_run.id]


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_transfers_silver_and_random_whitelist_loot_to_winner_guild(
    django_user_model,
    monkeypatch,
):
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "进攻帮")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "防守帮")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_finalize_tpl"),
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
            "warehouse_loot_percent": 10,
            "warehouse_loot_whitelist": ["grain", "gold_bar", "red_ruby"],
        },
    )
    monkeypatch.setattr(
        "guilds.services.guild_raid_loot.random.sample",
        lambda population, sample_size: [5, 15],
    )

    from guilds.services.guild_raids import process_guild_raid_battle

    assert process_guild_raid_battle(run, now=now) is True

    attacker_guild.refresh_from_db()
    defender_guild.refresh_from_db()
    run.refresh_from_db()

    assert attacker_guild.silver == 8000
    assert defender_guild.silver == 92000
    assert GuildWarehouse.objects.get(guild=attacker_guild, item_key="grain").quantity == 1
    assert GuildWarehouse.objects.get(guild=attacker_guild, item_key="red_ruby").quantity == 1
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="gold_bar").exists() is False
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="guild_badge").exists() is False
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="grain").quantity == 9
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="gold_bar").quantity == 5
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="red_ruby").quantity == 4
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="guild_badge").quantity == 9
    assert run.status == GuildRaidRun.Status.RETURNING
    assert run.is_attacker_victory is True
    assert run.loot_silver == 8000
    assert run.loot_items == {"grain": 1, "red_ruby": 1}
    assert run.battle_report_id == report.id
    assert run.completed_at is None


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_grants_salvage_to_defender_warehouse_on_defense_success(
    django_user_model,
    monkeypatch,
):
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "进攻败北")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "守成")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_defense_salvage_tpl"),
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
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="experience_fruit").quantity == 3
    assert GuildWarehouse.objects.get(guild=defender_guild, item_key="iron_sword").quantity == 2


@pytest.mark.django_db(transaction=True)
def test_process_guild_raid_battle_uses_and_applies_defender_troops(django_user_model, monkeypatch):
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "进攻测防")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "守方护院")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_defender_troops_tpl"),
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
        travel_time=300,
        battle_at=now,
        return_at=now + timedelta(seconds=300),
    )

    from battle.models import TroopTemplate

    defender_troop_template = TroopTemplate.objects.create(key="guild_defense_archer", name="守方弓手")
    GuildTroopStorage.objects.create(guild=defender_guild, troop_template=defender_troop_template, count=8)
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={"guild_defense_archer": 8},
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
        return report

    monkeypatch.setattr("guilds.services.guild_raids.execute_battle", _fake_execute_battle)
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
    assert captured["defender_setup"] == {"troop_loadout": {"guild_defense_archer": 8}}
    assert defender_storage.count == 5


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_raid_marks_returning_run_completed_and_returns_surviving_troops(
    django_user_model,
):
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "返程帮")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "终点帮")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_return_tpl"),
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

    from battle.models import TroopTemplate

    troop_template = TroopTemplate.objects.create(key="guild_return_archer", name="返程弓手")
    GuildTroopStorage.objects.create(guild=attacker_guild, troop_template=troop_template, count=0)

    from guilds.services.guild_raids import finalize_guild_raid

    assert finalize_guild_raid(run, now=now) is True

    run.refresh_from_db()
    storage = GuildTroopStorage.objects.get(guild=attacker_guild, troop_template=troop_template)
    assert run.status == GuildRaidRun.Status.COMPLETED
    assert run.completed_at == now
    assert storage.count == 6
