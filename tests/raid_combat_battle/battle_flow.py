from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import TroopTemplate
from core.config import BUILDING_KEYS
from core.exceptions import BattlePreparationError
from gameplay.models import PlayerTechnology, PlayerTroop, RaidRun
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.raid.combat import runs as combat_runs
from guests.models import Guest, GuestStatus, GuestTemplate
from tests.raid_combat_battle.support import build_attacker_defender


def test_dispatch_complete_raid_task_uses_remaining_return_time(monkeypatch):
    now = timezone.now()
    captured: dict[str, object] = {}

    def _fake_safe_apply_async(task, args, countdown, logger, log_message):
        captured["task"] = task
        captured["args"] = args
        captured["countdown"] = countdown

    import gameplay.tasks as gameplay_tasks

    fake_complete_task = object()
    monkeypatch.setattr(gameplay_tasks, "complete_raid_task", fake_complete_task, raising=False)
    monkeypatch.setattr(combat_battle, "safe_apply_async", _fake_safe_apply_async)

    run = SimpleNamespace(id=42, return_at=now + timedelta(seconds=37), travel_time=600)
    combat_battle._dispatch_complete_raid_task(run, now=now)

    assert captured["task"] is fake_complete_task
    assert captured["args"] == [42]
    assert captured["countdown"] == 37


def test_dispatch_complete_raid_task_finalizes_sync_when_due_dispatch_fails(monkeypatch):
    now = timezone.now()
    finalized: list[tuple[int, object]] = []

    import gameplay.tasks as gameplay_tasks

    monkeypatch.setattr(gameplay_tasks, "complete_raid_task", object(), raising=False)
    monkeypatch.setattr(combat_battle, "safe_apply_async", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(combat_runs, "finalize_raid", lambda run, now=None: finalized.append((run.id, now)))

    run = SimpleNamespace(id=77, return_at=now, travel_time=600)
    combat_battle._dispatch_complete_raid_task(run, now=now)

    assert finalized == [(77, now)]


def test_dispatch_complete_raid_task_finalizes_sync_when_task_import_fails(monkeypatch):
    now = timezone.now()
    finalized: list[tuple[int, object]] = []

    def _missing_module(_name):
        exc = ModuleNotFoundError("No module named 'gameplay.tasks'")
        exc.name = "gameplay.tasks"
        raise exc

    monkeypatch.setattr(combat_battle, "import_module", _missing_module)
    monkeypatch.setattr(combat_runs, "finalize_raid", lambda run, now=None: finalized.append((run.id, now)))

    run = SimpleNamespace(id=88, return_at=now, travel_time=600)
    combat_battle._dispatch_complete_raid_task(run, now=now)

    assert finalized == [(88, now)]


def test_dispatch_complete_raid_task_nested_import_error_bubbles_up(monkeypatch):
    now = timezone.now()

    def _nested_import_failure(_name):
        exc = ModuleNotFoundError("No module named 'redis'")
        exc.name = "redis"
        raise exc

    monkeypatch.setattr(combat_battle, "import_module", _nested_import_failure)

    run = SimpleNamespace(id=89, return_at=now, travel_time=600)

    with pytest.raises(ModuleNotFoundError, match="redis"):
        combat_battle._dispatch_complete_raid_task(run, now=now)


@pytest.mark.django_db
def test_apply_defeat_protection_sets_defender_until(django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_defeat_attacker",
        defender_username="raid_defeat_defender",
    )

    run = RaidRun.objects.create(attacker=attacker, defender=defender)
    now = timezone.now()
    combat_battle._apply_defeat_protection(run, is_attacker_victory=True, now=now)

    defender.refresh_from_db()
    expected = now + timedelta(seconds=combat_battle.PVPConstants.RAID_DEFEAT_PROTECTION_SECONDS)
    assert defender.defeat_protection_until is not None
    assert abs((defender.defeat_protection_until - expected).total_seconds()) <= 1


@pytest.mark.django_db
def test_apply_raid_loot_passes_raid_context_to_loot_calculation(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_loot_context_a",
        defender_username="raid_loot_context_d",
    )
    guest = SimpleNamespace(id=1001, level=10)
    guest_manager = SimpleNamespace(all=lambda: [guest])
    report = SimpleNamespace(losses={"attacker": {"casualties": [{"key": "dao_ke", "lost": 5}]}})
    locked_run = SimpleNamespace(
        attacker=attacker,
        defender_id=defender.id,
        guests=guest_manager,
        troop_loadout={"dao_ke": 20},
        battle_report=report,
        loot_resources={},
        loot_items={},
    )
    captured: dict[str, object] = {}

    def _fake_calculate_loot(locked_defender, *, guests, troop_loadout, battle_report):
        captured["defender_id"] = locked_defender.id
        captured["guests"] = guests
        captured["troop_loadout"] = troop_loadout
        captured["battle_report"] = battle_report
        return {"grain": 12}, {}

    monkeypatch.setattr(combat_battle, "_calculate_loot", _fake_calculate_loot)
    monkeypatch.setattr(
        combat_battle,
        "_apply_loot",
        lambda _defender, loot_resources, loot_items, locked_manor=None: (loot_resources, loot_items),
    )

    with transaction.atomic():
        combat_battle._apply_raid_loot_if_needed(locked_run, is_attacker_victory=True)

    assert captured == {
        "defender_id": defender.id,
        "guests": [guest],
        "troop_loadout": {"dao_ke": 20},
        "battle_report": report,
    }
    assert locked_run.loot_resources == {"grain": 12}
    assert locked_run.loot_items == {}


@pytest.mark.django_db
def test_execute_raid_battle_uses_attacker_snapshot(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_snapshot_a",
        defender_username="raid_snapshot_d",
    )

    template = GuestTemplate.objects.create(
        key="raid_snapshot_tpl",
        name="踢馆快照门客",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    attacker_guest = Guest.objects.create(
        manor=attacker,
        template=template,
        status=GuestStatus.DEPLOYED,
        level=20,
        force=300,
        intellect=120,
        defense_stat=130,
        agility=110,
        current_hp=900,
    )
    defender_guest = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=10,
        force=120,
        intellect=100,
        defense_stat=110,
        agility=90,
        current_hp=700,
    )
    attacker_stats = attacker_guest.stat_block()
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={},
        travel_time=60,
        battle_at=timezone.now(),
        return_at=timezone.now(),
        guest_snapshots=[
            {
                "guest_id": attacker_guest.id,
                "template_key": template.key,
                "display_name": attacker_guest.display_name,
                "rarity": attacker_guest.rarity,
                "status": "deployed",
                "level": 20,
                "force": 300,
                "intellect": 120,
                "defense_stat": 130,
                "agility": 110,
                "luck": 50,
                "attack": int(attacker_stats["attack"]),
                "defense": int(attacker_stats["defense"]),
                "max_hp": attacker_guest.max_hp,
                "current_hp": 900,
                "troop_capacity": int(getattr(attacker_guest, "troop_capacity", 0) or 0),
                "skill_keys": [],
            }
        ],
    )
    run.guests.add(attacker_guest)

    attacker_guest.level = 99
    attacker_guest.force = 9999
    attacker_guest.save(update_fields=["level", "force"])

    captured = {}

    def _fake_simulate_report(**kwargs):
        attacker_guests = kwargs.get("attacker_guests") or []
        assert attacker_guests
        captured["level"] = attacker_guests[0].level
        captured["force"] = attacker_guests[0].force
        captured["guest_id"] = attacker_guests[0].id
        return SimpleNamespace(
            winner="attacker",
            attacker_team=[{"guest_id": attacker_guest.id, "remaining_hp": 500}],
            defender_team=[{"guest_id": defender_guest.id, "remaining_hp": 300}],
            losses={
                "attacker": {"hp_updates": {str(attacker_guest.id): 500}},
                "defender": {"hp_updates": {str(defender_guest.id): 300}},
            },
        )

    monkeypatch.setattr("battle.services.simulate_report", _fake_simulate_report)

    combat_battle._execute_raid_battle(run)

    attacker_guest.refresh_from_db()
    assert captured["level"] == 20
    assert captured["force"] == 300
    assert captured["guest_id"] == attacker_guest.id
    assert attacker_guest.current_hp == 500


@pytest.mark.django_db
def test_execute_raid_battle_uses_all_idle_defenders_even_when_max_squad_is_lower(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_defender_all_idle_a",
        defender_username="raid_defender_all_idle_d",
    )
    monkeypatch.setattr(defender, "get_building_level", lambda _key: 0)

    template = GuestTemplate.objects.create(
        key="raid_defender_all_idle_tpl",
        name="踢馆守方门客",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    attacker_guest = Guest.objects.create(
        manor=attacker,
        template=template,
        status=GuestStatus.DEPLOYED,
        level=20,
        force=300,
        intellect=120,
        defense_stat=130,
        agility=110,
        current_hp=900,
    )
    defender_guest_1 = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=20,
        force=180,
        intellect=90,
        defense_stat=100,
        agility=95,
        current_hp=1200,
    )
    defender_guest_2 = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=18,
        force=170,
        intellect=88,
        defense_stat=98,
        agility=92,
        current_hp=1180,
    )
    defender_guest_3 = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=16,
        force=160,
        intellect=85,
        defense_stat=96,
        agility=90,
        current_hp=1160,
    )
    defender_guest_4 = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=14,
        force=150,
        intellect=84,
        defense_stat=94,
        agility=88,
        current_hp=1140,
    )
    excluded_guest = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.WORKING,
        level=30,
        force=250,
        intellect=100,
        defense_stat=120,
        agility=100,
        current_hp=1300,
    )
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={},
        travel_time=60,
        battle_at=timezone.now(),
        return_at=timezone.now(),
    )
    run.guests.add(attacker_guest)

    captured: dict[str, object] = {}

    def _fake_simulate_report(**kwargs):
        captured["defender_guest_ids"] = [guest.id for guest in kwargs["defender_guests"]]
        captured["defender_max_squad"] = kwargs["defender_max_squad"]
        return SimpleNamespace(winner="attacker")

    monkeypatch.setattr("battle.services.simulate_report", _fake_simulate_report)
    monkeypatch.setattr(combat_battle, "_apply_guest_damage_from_report", lambda *_args, **_kwargs: None)

    combat_battle._execute_raid_battle(run)

    assert captured["defender_guest_ids"] == [
        defender_guest_1.id,
        defender_guest_2.id,
        defender_guest_3.id,
        defender_guest_4.id,
    ]
    assert captured["defender_max_squad"] == 4
    assert excluded_guest.id not in captured["defender_guest_ids"]


@pytest.mark.django_db
def test_execute_raid_battle_passes_defender_technology_levels(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_defender_tech_a",
        defender_username="raid_defender_tech_d",
    )

    template = GuestTemplate.objects.create(
        key="raid_defender_tech_tpl",
        name="踢馆科技门客",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    attacker_guest = Guest.objects.create(
        manor=attacker,
        template=template,
        status=GuestStatus.DEPLOYED,
        level=20,
        force=300,
        intellect=120,
        defense_stat=130,
        agility=110,
        current_hp=900,
    )
    defender_guest = Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=20,
        force=180,
        intellect=90,
        defense_stat=100,
        agility=95,
        current_hp=1200,
    )
    PlayerTechnology.objects.create(manor=defender, tech_key="gong_attack", level=10)
    PlayerTechnology.objects.create(manor=defender, tech_key="gong_hp", level=8)

    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={},
        travel_time=60,
        battle_at=timezone.now(),
        return_at=timezone.now(),
    )
    run.guests.add(attacker_guest)

    captured: dict[str, object] = {}

    def _fake_simulate_report(**kwargs):
        captured["defender_setup"] = kwargs["defender_setup"]
        captured["defender_manor"] = kwargs["defender_manor"]
        return SimpleNamespace(
            winner="attacker",
            attacker_team=[{"guest_id": attacker_guest.id, "remaining_hp": 500}],
            defender_team=[{"guest_id": defender_guest.id, "remaining_hp": 300}],
            losses={
                "attacker": {"hp_updates": {str(attacker_guest.id): 500}},
                "defender": {"hp_updates": {str(defender_guest.id): 300}},
            },
        )

    monkeypatch.setattr("battle.services.simulate_report", _fake_simulate_report)

    combat_battle._execute_raid_battle(run)

    assert captured["defender_setup"] == {
        "troop_loadout": {},
        "technology": {"levels": {"gong_attack": 10, "gong_hp": 8}},
    }
    assert captured["defender_manor"] == defender


@pytest.mark.django_db
def test_execute_raid_battle_persists_defender_city_defense_damage(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_city_defense_damage_a",
        defender_username="raid_city_defense_damage_d",
    )
    wall = defender.buildings.select_related("building_type").get(building_type__key=BUILDING_KEYS.WALL)
    wall.current_hp = 3000
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])

    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={},
        travel_time=60,
        battle_at=timezone.now(),
        return_at=timezone.now(),
    )

    def _fake_simulate_report(**_kwargs):
        return SimpleNamespace(
            winner="attacker",
            defender_city_defenses=[
                {
                    "key": BUILDING_KEYS.WALL,
                    "hp": 123,
                }
            ],
        )

    monkeypatch.setattr("battle.services.simulate_report", _fake_simulate_report)
    monkeypatch.setattr(combat_battle, "_apply_guest_damage_from_report", lambda *_args, **_kwargs: None)

    combat_battle._execute_raid_battle(run)

    wall.refresh_from_db()
    assert wall.current_hp == 123


@pytest.mark.django_db
def test_process_raid_battle_cleans_up_run_when_manor_lock_fails(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_cleanup_a",
        defender_username="raid_cleanup_d",
    )

    troop_template = TroopTemplate.objects.create(key="raid_cleanup_guard", name="清理护院")
    troop = PlayerTroop.objects.create(manor=attacker, troop_template=troop_template, count=2)
    guest_template = GuestTemplate.objects.create(
        key="raid_cleanup_guest",
        name="清理门客",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1200,
    )
    guest = Guest.objects.create(
        manor=attacker,
        template=guest_template,
        status=GuestStatus.DEPLOYED,
        level=10,
        force=100,
        intellect=90,
        defense_stat=95,
        agility=80,
        current_hp=guest_template.base_hp,
    )
    now = timezone.now()
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={"raid_cleanup_guard": 3},
        travel_time=60,
        battle_at=now,
        return_at=now,
    )
    run.guests.add(guest)

    monkeypatch.setattr(
        combat_battle,
        "_lock_battle_manors",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BattlePreparationError("目标庄园不存在")),
    )

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    guest.refresh_from_db()
    troop.refresh_from_db()

    assert run.status == RaidRun.Status.COMPLETED
    assert run.completed_at is not None
    assert run.return_at is not None
    assert run.is_attacker_victory is False
    assert guest.status == GuestStatus.IDLE
    assert troop.count == 5
