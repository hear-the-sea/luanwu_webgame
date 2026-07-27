from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from battle.deployment import collect_active_deployment_guest_ids
from battle.models import BattleReport, TroopTemplate
from core.config import BUILDING_KEYS
from core.exceptions import BattlePreparationError
from gameplay.models import PlayerTechnology, PlayerTroop, RaidRun
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.raid.combat import runs as combat_runs
from gameplay.services.raid.combat.travel import get_active_raid_count
from guests.models import Guest, GuestStatus, GuestTemplate
from tests.raid_combat_battle.support import build_attacker_defender, build_run, stub_process_raid_battle_happy_path


def test_raid_run_failure_contract_is_explicit_and_constrained():
    statuses = dict(RaidRun.Status.choices)
    failure_reasons = dict(RaidRun.FailureReason.choices)

    assert statuses[RaidRun.Status.FAILED] == "出征失败"
    assert failure_reasons[RaidRun.FailureReason.MISSING_ATTACKER_LINEUP] == "缺少出征门客与快照"
    assert failure_reasons[RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT] == "门客战斗快照无效"
    assert failure_reasons[RaidRun.FailureReason.INVALID_TROOP_LOADOUT] == "护院编队快照无效"
    field = RaidRun._meta.get_field("failure_reason")
    assert field.default == ""
    assert field.max_length == 64


def test_raid_run_admin_exposes_failure_reason():
    from django.contrib import admin

    model_admin = admin.site._registry[RaidRun]
    assert "failure_reason" in model_admin.readonly_fields


@pytest.mark.django_db
def test_failed_raid_is_terminal_and_excluded_from_active_deployments(django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_failed_terminal_a",
        defender_username="raid_failed_terminal_d",
    )
    template = GuestTemplate.objects.create(
        key="raid_failed_terminal_tpl",
        name="失败终态门客",
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
        template=template,
        status=GuestStatus.DEPLOYED,
        level=10,
        force=100,
        intellect=90,
        defense_stat=95,
        agility=80,
        current_hp=template.base_hp,
    )
    now = timezone.now()
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.FAILED,
        failure_reason=RaidRun.FailureReason.MISSING_ATTACKER_LINEUP,
        battle_at=now + timedelta(minutes=1),
        return_at=now + timedelta(minutes=2),
        completed_at=now,
    )
    run.guests.add(guest)

    assert run.time_remaining == 0
    assert run.next_state_at is None
    assert run.can_retreat is False
    assert get_active_raid_count(attacker) == 0
    assert combat_runs.get_active_raids(attacker) == []
    assert collect_active_deployment_guest_ids([guest.id]) == set()


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


def test_process_raid_battle_recovers_missing_return_deadline(monkeypatch):
    now = timezone.now()
    attacker = SimpleNamespace(id=1)
    defender = SimpleNamespace(id=2)
    run = build_run(run_id=90, attacker=attacker, defender=defender)
    run.return_at = None
    run.travel_time = 60
    report = SimpleNamespace(winner="defender")
    stub_process_raid_battle_happy_path(monkeypatch, run, attacker, defender, report)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dismiss_marching_raids_if_protected", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "safe_apply_async", lambda *_args, **_kwargs: False)
    finalized = []
    monkeypatch.setattr(combat_runs, "finalize_raid", lambda *_args, **_kwargs: finalized.append(run.id))

    combat_battle.process_raid_battle(run, now=now)

    assert run.status == RaidRun.Status.RETURNING
    assert run.return_at == now + timedelta(seconds=60)
    assert finalized == []


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
        base_seed=101,
        rng_version=1,
    )
    captured: dict[str, object] = {}

    def _fake_calculate_loot(locked_defender, *, rng, guests, troop_loadout, battle_report):
        captured["rng"] = rng
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

    assert captured["defender_id"] == defender.id
    assert captured["guests"] == [guest]
    assert captured["troop_loadout"] == {"dao_ke": 20}
    assert captured["battle_report"] is report
    assert hasattr(captured["rng"], "random")
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
def test_execute_raid_battle_caps_and_orders_idle_defenders_and_only_applies_selected_damage(
    monkeypatch,
    django_user_model,
):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_defender_all_idle_a",
        defender_username="raid_defender_all_idle_d",
    )
    monkeypatch.setattr(defender, "get_building_level", lambda _key: 0)

    green_template = GuestTemplate.objects.create(
        key="raid_defender_limit_green_tpl",
        name="踢馆绿色守方门客",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    blue_template = GuestTemplate.objects.create(
        key="raid_defender_limit_blue_tpl",
        name="踢馆蓝色守方门客",
        archetype="military",
        rarity="blue",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    purple_template = GuestTemplate.objects.create(
        key="raid_defender_limit_purple_tpl",
        name="踢馆紫色守方门客",
        archetype="military",
        rarity="purple",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    attacker_guest = Guest.objects.create(
        manor=attacker,
        template=green_template,
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
        template=green_template,
        status=GuestStatus.IDLE,
        level=99,
        force=180,
        intellect=90,
        defense_stat=100,
        agility=95,
        current_hp=1200,
    )
    defender_guest_2 = Guest.objects.create(
        manor=defender,
        template=blue_template,
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
        template=purple_template,
        status=GuestStatus.IDLE,
        level=1,
        force=160,
        intellect=85,
        defense_stat=96,
        agility=90,
        current_hp=1160,
    )
    defender_guest_4 = Guest.objects.create(
        manor=defender,
        template=blue_template,
        status=GuestStatus.IDLE,
        level=18,
        force=150,
        intellect=84,
        defense_stat=94,
        agility=88,
        current_hp=1140,
    )
    excluded_guest = Guest.objects.create(
        manor=defender,
        template=purple_template,
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
        return SimpleNamespace(
            winner="attacker",
            attacker_team=[],
            defender_team=[
                {"guest_id": defender_guest_1.id, "remaining_hp": 101},
                {"guest_id": defender_guest_2.id, "remaining_hp": 202},
                {"guest_id": defender_guest_3.id, "remaining_hp": 303},
                {"guest_id": defender_guest_4.id, "remaining_hp": 404},
                {"guest_id": excluded_guest.id, "remaining_hp": 505},
            ],
            losses={"attacker": {}, "defender": {}},
        )

    monkeypatch.setattr("battle.services.simulate_report", _fake_simulate_report)

    combat_battle._execute_raid_battle(run)

    assert captured["defender_guest_ids"] == [
        defender_guest_3.id,
        defender_guest_2.id,
        defender_guest_4.id,
    ]
    assert captured["defender_max_squad"] == 3
    defender_guest_1.refresh_from_db()
    defender_guest_2.refresh_from_db()
    defender_guest_3.refresh_from_db()
    defender_guest_4.refresh_from_db()
    excluded_guest.refresh_from_db()
    assert defender_guest_1.current_hp == 1200
    assert defender_guest_2.current_hp == 202
    assert defender_guest_3.current_hp == 303
    assert defender_guest_4.current_hp == 404
    assert excluded_guest.current_hp == 1300
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


@pytest.mark.django_db(transaction=True)
def test_process_raid_battle_accepts_snapshot_only_lineup(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_snapshot_only_a",
        defender_username="raid_snapshot_only_d",
    )
    template = GuestTemplate.objects.create(
        key="raid_snapshot_only_tpl",
        name="仅快照门客",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    guest = Guest.objects.create(
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
    now = timezone.now()
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=build_guest_battle_snapshots([guest], include_identity=True),
        travel_time=60,
        battle_at=now,
        return_at=now + timedelta(minutes=1),
    )
    assert not run.guests.exists()

    captured: dict[str, object] = {}

    def _simulate_report(**kwargs):
        attacker_guests = kwargs.get("attacker_guests") or []
        captured["guest_ids"] = [snapshot_guest.id for snapshot_guest in attacker_guests]
        captured["forces"] = [snapshot_guest.force for snapshot_guest in attacker_guests]
        report = BattleReport.objects.create(
            manor=attacker,
            opponent_name=defender.display_name,
            battle_type="raid",
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
        captured["report_id"] = report.pk
        return report

    monkeypatch.setattr("battle.services.simulate_report", _simulate_report)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dismiss_marching_raids_if_protected", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dispatch_complete_raid_task", lambda *_args, **_kwargs: None)

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    assert captured["guest_ids"] == [guest.id]
    assert captured["forces"] == [300]
    assert run.status == RaidRun.Status.RETURNING
    assert run.failure_reason == ""
    assert run.battle_report_id == captured["report_id"]


@pytest.mark.django_db(transaction=True)
def test_process_raid_battle_marks_missing_attacker_lineup_failed_and_returns_troops(
    monkeypatch,
    django_user_model,
    caplog,
):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_invalid_lineup_a",
        defender_username="raid_invalid_lineup_d",
    )
    now = timezone.now()
    troop_template = TroopTemplate.objects.create(key="raid_missing_lineup_guard", name="缺阵护院")
    troop = PlayerTroop.objects.create(manor=attacker, troop_template=troop_template, count=0)
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={troop_template.key: 7},
        guest_snapshots=[],
        battle_at=now - timedelta(seconds=1),
    )
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    caplog.set_level("ERROR", logger="gameplay.services.raid.combat.battle")

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    assert run.status == RaidRun.Status.FAILED
    assert run.failure_reason == RaidRun.FailureReason.MISSING_ATTACKER_LINEUP
    assert run.base_seed > 0
    assert run.rng_version > 0
    assert run.battle_engine_version != "legacy"
    assert run.completed_at == now
    assert run.is_attacker_victory is None
    assert run.battle_report_id is None
    assert run.troop_loadout == {troop_template.key: 7}
    assert run.resources_released is True
    troop.refresh_from_db()
    assert troop.count == 7
    assert any(getattr(record, "component", None) == "raid_failed_and_resources_released" for record in caplog.records)


@pytest.mark.django_db(transaction=True)
def test_process_raid_battle_invalid_lineup_recovery_is_idempotent(monkeypatch, django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_invalid_idempotent_a",
        defender_username="raid_invalid_idempotent_d",
    )
    first_now = timezone.now()
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=[],
        battle_at=first_now - timedelta(seconds=1),
    )
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)

    combat_battle.process_raid_battle(run, now=first_now)
    combat_battle.process_raid_battle(run, now=first_now + timedelta(minutes=1))

    run.refresh_from_db()
    assert run.status == RaidRun.Status.FAILED
    assert run.completed_at == first_now
    assert run.failure_reason == RaidRun.FailureReason.MISSING_ATTACKER_LINEUP


@pytest.mark.django_db(transaction=True)
def test_process_raid_battle_invalid_snapshot_payload_fails_and_releases_resources_once(
    monkeypatch,
    django_user_model,
):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_invalid_snapshot_a",
        defender_username="raid_invalid_snapshot_d",
    )
    now = timezone.now()
    troop_template = TroopTemplate.objects.create(key="raid_invalid_snapshot_guard", name="坏快照护院")
    troop = PlayerTroop.objects.create(manor=attacker, troop_template=troop_template, count=0)
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots={"unexpected": "mapping"},
        troop_loadout={troop_template.key: 4},
        battle_at=now - timedelta(seconds=1),
    )
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)

    combat_battle.process_raid_battle(run, now=now)
    combat_battle.process_raid_battle(run, now=now + timedelta(minutes=1))

    run.refresh_from_db()
    troop.refresh_from_db()
    assert run.status == RaidRun.Status.FAILED
    assert run.failure_reason == RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT
    assert run.resources_released is True
    assert run.completed_at == now
    assert troop.count == 4


@pytest.mark.parametrize("bad_snapshot_kind", ["empty", "string", "invalid_numeric"])
@pytest.mark.django_db(transaction=True)
def test_process_raid_battle_isolates_invalid_snapshot_entries(
    monkeypatch,
    django_user_model,
    bad_snapshot_kind,
):
    from tests.raid_combat_battle.support import build_real_raid_cleanup_fixture

    _attacker, _defender, troop, guest, run, now = build_real_raid_cleanup_fixture(django_user_model)
    if bad_snapshot_kind == "empty":
        snapshots = [{}]
    elif bad_snapshot_kind == "string":
        snapshots = ["bad-snapshot"]
    else:
        payload = build_guest_battle_snapshots([guest], include_identity=True)[0]
        payload["level"] = 0
        snapshots = [payload]
    run.guest_snapshots = snapshots
    run.save(update_fields=["guest_snapshots"])
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    guest.refresh_from_db()
    troop.refresh_from_db()
    assert run.status == RaidRun.Status.FAILED
    assert run.failure_reason == RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT
    assert run.resources_released is True
    assert guest.status == GuestStatus.IDLE
    assert troop.count == 5
