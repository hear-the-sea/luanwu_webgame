from __future__ import annotations

import contextlib
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from battle.models import TroopTemplate
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, CURRENT_RNG_VERSION
from gameplay.models import PlayerTroop, RaidRun
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import battle as combat_battle
from guests.models import Guest, GuestStatus, GuestTemplate


def build_attacker_defender(django_user_model, *, attacker_username: str, defender_username: str):
    attacker_user = django_user_model.objects.create_user(username=attacker_username, password="pass123")
    defender_user = django_user_model.objects.create_user(username=defender_username, password="pass123")
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    return attacker, defender


def build_run(*, run_id: int, attacker, defender, save_counter: dict[str, int] | None = None):
    if save_counter is None:

        def save_fn(**_kwargs):
            return None

    else:

        def save_fn(**_kwargs):
            save_counter["count"] = save_counter["count"] + 1
            return None

    return SimpleNamespace(
        pk=run_id,
        id=run_id,
        attacker_id=attacker.id,
        defender_id=defender.id,
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        base_seed=101,
        rng_version=CURRENT_RNG_VERSION,
        battle_engine_version=CURRENT_BATTLE_ENGINE_VERSION,
        travel_time=0,
        return_at=None,
        save=save_fn,
    )


def build_locked_run(
    *,
    run_id: int,
    now,
    save_fields: dict[str, object],
    reason_target="守方",
    elapsed_seconds: int = 15,
    travel_time: int = 60,
):
    started_at = now - timedelta(seconds=elapsed_seconds)
    return SimpleNamespace(
        id=run_id,
        attacker_id=1,
        defender_id=2,
        attacker=SimpleNamespace(id=1),
        defender=SimpleNamespace(display_name=reason_target),
        started_at=started_at,
        battle_at=started_at + timedelta(seconds=travel_time),
        travel_time=travel_time,
        return_at=None,
        save=lambda *, update_fields: save_fields.__setitem__("fields", update_fields),
    )


def build_real_raid_cleanup_fixture(django_user_model):
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
    return attacker, defender, troop, guest, run, now


def stub_process_raid_battle_happy_path(monkeypatch, run, attacker, defender, report):
    monkeypatch.setattr(combat_battle.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(combat_battle, "_ensure_raid_replay_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_raid_loot_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
