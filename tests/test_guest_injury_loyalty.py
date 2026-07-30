from __future__ import annotations

import importlib
import math
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.utils import timezone

from battle.execution import apply_guest_hp_updates
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl.finalization_helpers import prepare_guest_updates_for_finalize
from gameplay.services.raid.combat.battle_guest_damage import apply_guest_damage_from_report
from guests import tasks as guest_tasks
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services import health as guest_health
from guests.services.health import heal_guest
from guests.services.loyalty import process_injury_loyalty_decay_for_guest


def _create_guest(django_user_model, *, username: str, loyalty: int = 10) -> Guest:
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key=f"{username}_template",
        name="重伤忠诚测试门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1000,
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=70,
        luck=50,
        loyalty=loyalty,
        current_hp=1,
    )


@pytest.mark.django_db
def test_injury_loyalty_decay_catches_up_complete_intervals_and_is_idempotent(django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_catchup", loyalty=5)
    now = timezone.now()
    guest.status = GuestStatus.INJURED
    guest.injury_loyalty_processed_at = now - timedelta(hours=7)
    guest.save(update_fields=["status", "injury_loyalty_processed_at"])

    processed_intervals = process_injury_loyalty_decay_for_guest(guest.id, now=now)

    guest.refresh_from_db()
    assert processed_intervals == 2
    assert guest.loyalty == 3
    assert guest.injury_loyalty_processed_at == now - timedelta(hours=1)
    assert process_injury_loyalty_decay_for_guest(guest.id, now=now) == 0


@pytest.mark.django_db
def test_injury_loyalty_scan_processes_due_guest_once(monkeypatch, django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_scan", loyalty=4)
    now = timezone.now()
    guest.status = GuestStatus.INJURED
    guest.injury_loyalty_processed_at = now - timedelta(hours=3)
    guest.save(update_fields=["status", "injury_loyalty_processed_at"])
    monkeypatch.setattr(guest_tasks.timezone, "now", lambda: now)

    assert guest_tasks.scan_injury_loyalty_decay(limit=10) == 1

    guest.refresh_from_db()
    assert guest.loyalty == 3
    assert guest.injury_loyalty_processed_at == now
    assert guest_tasks.scan_injury_loyalty_decay(limit=10) == 0


@pytest.mark.django_db
def test_injury_loyalty_migration_starts_existing_injured_guests_from_migration_time(
    monkeypatch,
    django_user_model,
):
    injured_guest = _create_guest(django_user_model, username="injury_loyalty_migration_injured")
    idle_guest = _create_guest(django_user_model, username="injury_loyalty_migration_idle")
    injured_guest.status = GuestStatus.INJURED
    injured_guest.save(update_fields=["status"])
    migration_time = timezone.now()
    migration = importlib.import_module("guests.migrations.0066_guest_injury_loyalty_processed_at")
    monkeypatch.setattr(migration.timezone, "now", lambda: migration_time)

    migration.initialize_existing_injured_guests(apps, None)

    injured_guest.refresh_from_db()
    idle_guest.refresh_from_db()
    assert injured_guest.injury_loyalty_processed_at == migration_time
    assert idle_guest.injury_loyalty_processed_at is None


@pytest.mark.django_db
def test_healing_settles_due_injury_decay_then_clears_its_clock(monkeypatch, django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_heal", loyalty=5)
    now = timezone.now()
    guest.status = GuestStatus.INJURED
    guest.injury_loyalty_processed_at = now - timedelta(hours=3)
    guest.save(update_fields=["status", "injury_loyalty_processed_at"])
    monkeypatch.setattr(guest_health.timezone, "now", lambda: now)

    result = heal_guest(guest, max(1, math.ceil(guest.max_hp * 0.2) - guest.current_hp))

    guest.refresh_from_db()
    assert result["injury_cured"] is True
    assert guest.status == GuestStatus.IDLE
    assert guest.loyalty == 4
    assert guest.injury_loyalty_processed_at is None


@pytest.mark.django_db
def test_timely_healing_clears_injury_clock_without_loyalty_loss(monkeypatch, django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_timely_heal", loyalty=5)
    now = timezone.now()
    guest.status = GuestStatus.INJURED
    guest.injury_loyalty_processed_at = now - timedelta(hours=2, minutes=59)
    guest.save(update_fields=["status", "injury_loyalty_processed_at"])
    monkeypatch.setattr(guest_health.timezone, "now", lambda: now)

    result = heal_guest(guest, max(1, math.ceil(guest.max_hp * 0.2) - guest.current_hp))

    guest.refresh_from_db()
    assert result["injury_cured"] is True
    assert guest.loyalty == 5
    assert guest.injury_loyalty_processed_at is None


@pytest.mark.django_db
def test_regular_battle_defeat_starts_injury_loyalty_clock(django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_regular_battle")

    apply_guest_hp_updates(
        [guest],
        [SimpleNamespace(guest_id=guest.id, hp=0)],
        apply_damage=True,
    )

    guest.refresh_from_db()
    assert guest.status == GuestStatus.INJURED
    assert guest.injury_loyalty_processed_at is not None


@pytest.mark.django_db
def test_mission_battle_defeat_starts_injury_loyalty_clock(django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_mission")
    now = timezone.now()

    guests_to_update, update_fields = prepare_guest_updates_for_finalize(
        [guest],
        is_retreating=False,
        defeated_guest_ids={guest.id},
        hp_updates={guest.id: 0},
        now=now,
    )
    Guest.objects.bulk_update(guests_to_update, update_fields)

    guest.refresh_from_db()
    assert guest.status == GuestStatus.INJURED
    assert guest.injury_loyalty_processed_at == now


@pytest.mark.django_db
def test_raid_battle_defeat_starts_injury_loyalty_clock(django_user_model):
    guest = _create_guest(django_user_model, username="injury_loyalty_raid")
    now = timezone.now()
    report = SimpleNamespace(
        attacker_team=[{"guest_id": guest.id, "remaining_hp": 0}],
        defender_team=[],
        losses={},
    )

    apply_guest_damage_from_report(
        report,
        attacker_guest_ids={guest.id},
        defender_guest_ids=set(),
        guest_model=Guest,
        guest_status=GuestStatus,
        now=now,
    )

    guest.refresh_from_db()
    assert guest.status == GuestStatus.INJURED
    assert guest.injury_loyalty_processed_at == now
