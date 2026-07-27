from __future__ import annotations

import threading
from datetime import timedelta

from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import RaidRun
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import battle as combat_battle
from guests.models import Guest, GuestStatus, GuestTemplate


def build_attacker_defender(django_user_model, *, attacker_username: str, defender_username: str):
    attacker_user = django_user_model.objects.create_user(username=attacker_username, password="pass123")
    defender_user = django_user_model.objects.create_user(username=defender_username, password="pass123")
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    return attacker, defender


def create_marching_run(attacker, defender, *, battle_due: bool = True) -> RaidRun:
    guest_template = GuestTemplate.objects.create(
        key=f"raid_concurrency_guest_{attacker.pk}",
        name="并发测试门客",
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
        guest_snapshots=build_guest_battle_snapshots([guest], include_identity=True),
        troop_loadout={},
        status=RaidRun.Status.MARCHING,
        travel_time=60,
        battle_at=now - timedelta(seconds=1) if battle_due else now + timedelta(seconds=60),
        return_at=now + timedelta(seconds=60 if battle_due else 120),
    )
    run.guests.add(guest)
    return run


def configure_battle_side_effects(monkeypatch, *, attacker, defender):
    executed_reports: list[int] = []
    dispatches: list[int] = []
    side_effect_lock = threading.Lock()

    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_raid_loot_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dismiss_marching_raids_if_protected", lambda *_args, **_kwargs: None)

    def _fake_execute(locked_run):
        report = BattleReport.objects.create(
            manor=locked_run.attacker,
            opponent_name=locked_run.defender.display_name,
            battle_type="raid",
            attacker_team=[],
            attacker_troops={},
            defender_team=[],
            defender_troops={},
            rounds=[],
            losses={},
            drops={},
            winner="attacker",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )
        with side_effect_lock:
            executed_reports.append(report.pk)
        return report

    def _fake_dispatch(locked_run, *, now=None):
        del now
        with side_effect_lock:
            dispatches.append(locked_run.pk)

    monkeypatch.setattr(combat_battle, "_execute_raid_battle", _fake_execute)
    monkeypatch.setattr(combat_battle, "_dispatch_complete_raid_task", _fake_dispatch)
    return executed_reports, dispatches
