from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.models import PlayerTroop, RaidRun
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate


def _create_deployed_guest(manor, *, key: str) -> Guest:
    template = GuestTemplate.objects.create(
        key=key,
        name=key,
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1200,
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.DEPLOYED,
        level=10,
        force=100,
        intellect=90,
        defense_stat=95,
        agility=80,
        current_hp=template.base_hp,
    )


@pytest.mark.parametrize(
    ("options", "option_name"),
    [
        ({"apply": True, "raid_run_id": [0]}, "--raid-run-id"),
        ({"apply": True, "raid_run_id": [1, -1]}, "--raid-run-id"),
        ({"apply": True, "guild_raid_run_id": [0]}, "--guild-raid-run-id"),
        ({"apply": True, "limit": 0}, "--limit"),
        ({"apply": True, "limit": -1}, "--limit"),
    ],
)
def test_audit_rejects_non_positive_ids_and_limits(options, option_name):
    with pytest.raises(CommandError, match=option_name):
        call_command("audit_pvp_arena_state", verbosity=0, **options)


@pytest.mark.parametrize(
    ("options", "option_name"),
    [
        ({"apply": True, "since": ""}, "--since"),
        ({"apply": True, "before": ""}, "--before"),
    ],
)
def test_audit_rejects_blank_time_bounds(options, option_name):
    with pytest.raises(CommandError, match=option_name):
        call_command("audit_pvp_arena_state", verbosity=0, **options)


@pytest.mark.django_db
def test_audit_pvp_arena_state_defaults_to_dry_run_without_writes(django_user_model, monkeypatch):
    attacker = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_dry_attacker"))
    defender = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_dry_defender"))
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=["invalid-snapshot"],
        troop_loadout={},
        battle_at=timezone.now(),
    )
    monkeypatch.setattr(
        "gameplay.management.commands.audit_pvp_arena_state.fail_raid_run_and_release_resources",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not invoke the compensation writer"),
    )
    stdout = StringIO()

    call_command(
        "audit_pvp_arena_state",
        raid_run_id=[run.pk],
        limit=10,
        stdout=stdout,
        verbosity=0,
    )

    run.refresh_from_db()
    output = stdout.getvalue()
    assert "mode=dry-run findings=1" in output
    assert '"model": "RaidRun"' in output
    assert '"suggested_action": "fail_and_release_resources"' in output
    assert run.status == RaidRun.Status.MARCHING
    assert run.failure_reason == ""
    assert run.resources_released is False


@pytest.mark.django_db
def test_audit_accepts_empty_legacy_snapshots_when_attached_guests_exist(django_user_model, monkeypatch):
    attacker = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_compat_attacker"))
    defender = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_compat_defender"))
    guest = _create_deployed_guest(attacker, key="pvp_audit_compat_guest")
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=[],
        troop_loadout={},
        battle_at=timezone.now() + timedelta(minutes=5),
    )
    run.guests.add(guest)
    monkeypatch.setattr(
        "gameplay.management.commands.audit_pvp_arena_state.fail_raid_run_and_release_resources",
        lambda *_args, **_kwargs: pytest.fail("compatible raid must not be failed by the audit"),
    )
    stdout = StringIO()

    call_command(
        "audit_pvp_arena_state",
        apply=True,
        raid_run_id=[run.pk],
        limit=10,
        stdout=stdout,
        verbosity=0,
    )

    run.refresh_from_db()
    guest.refresh_from_db()
    assert "mode=apply findings=0" in stdout.getvalue()
    assert run.status == RaidRun.Status.MARCHING
    assert run.failure_reason == ""
    assert run.resources_released is False
    assert guest.status == GuestStatus.DEPLOYED


@pytest.mark.django_db(transaction=True)
def test_audit_repairs_legacy_failed_raid_resources_once_and_preserves_completion_time(django_user_model):
    attacker = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_legacy_attacker"))
    defender = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_legacy_defender"))
    guest = _create_deployed_guest(attacker, key="pvp_audit_legacy_guest")
    troop_template = TroopTemplate.objects.create(key="pvp_audit_legacy_guard", name="历史补偿护院")
    troop = PlayerTroop.objects.create(manor=attacker, troop_template=troop_template, count=2)
    original_completed_at = timezone.now() - timedelta(days=1)
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.FAILED,
        failure_reason=RaidRun.FailureReason.MISSING_ATTACKER_LINEUP,
        resources_released=False,
        guest_snapshots=[],
        troop_loadout={troop_template.key: 3},
        completed_at=original_completed_at,
    )
    run.guests.add(guest)

    dry_run_stdout = StringIO()
    call_command(
        "audit_pvp_arena_state",
        raid_run_id=[run.pk],
        limit=10,
        stdout=dry_run_stdout,
        verbosity=0,
    )

    run.refresh_from_db()
    guest.refresh_from_db()
    troop.refresh_from_db()
    dry_run_output = dry_run_stdout.getvalue()
    assert "mode=dry-run findings=1" in dry_run_output
    assert '"suggested_action": "release_legacy_failed_resources"' in dry_run_output
    assert run.resources_released is False
    assert run.completed_at == original_completed_at
    assert guest.status == GuestStatus.DEPLOYED
    assert troop.count == 2

    apply_stdout = StringIO()
    call_command(
        "audit_pvp_arena_state",
        apply=True,
        raid_run_id=[run.pk],
        limit=10,
        stdout=apply_stdout,
        verbosity=0,
    )

    run.refresh_from_db()
    guest.refresh_from_db()
    troop.refresh_from_db()
    assert "mode=apply findings=1" in apply_stdout.getvalue()
    assert '"applied": true' in apply_stdout.getvalue()
    assert run.status == RaidRun.Status.FAILED
    assert run.failure_reason == RaidRun.FailureReason.MISSING_ATTACKER_LINEUP
    assert run.resources_released is True
    assert run.completed_at == original_completed_at
    assert guest.status == GuestStatus.IDLE
    assert troop.count == 5

    second_apply_stdout = StringIO()
    call_command(
        "audit_pvp_arena_state",
        apply=True,
        raid_run_id=[run.pk],
        limit=10,
        stdout=second_apply_stdout,
        verbosity=0,
    )
    troop.refresh_from_db()
    assert "mode=apply findings=0" in second_apply_stdout.getvalue()
    assert troop.count == 5


@pytest.mark.django_db
def test_audit_does_not_auto_compensate_failed_raids_outside_the_known_legacy_reason(django_user_model):
    attacker = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_unknown_failed_attacker"))
    defender = ensure_manor(django_user_model.objects.create_user(username="pvp_audit_unknown_failed_defender"))
    troop_template = TroopTemplate.objects.create(key="pvp_audit_unknown_failed_guard", name="未知失败护院")
    troop = PlayerTroop.objects.create(manor=attacker, troop_template=troop_template, count=2)
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.FAILED,
        failure_reason=RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT,
        resources_released=False,
        guest_snapshots=["invalid-snapshot"],
        troop_loadout={troop_template.key: 3},
        completed_at=timezone.now() - timedelta(days=1),
    )
    stdout = StringIO()

    call_command(
        "audit_pvp_arena_state",
        apply=True,
        raid_run_id=[run.pk],
        limit=10,
        stdout=stdout,
        verbosity=0,
    )

    run.refresh_from_db()
    troop.refresh_from_db()
    assert "mode=apply findings=0" in stdout.getvalue()
    assert run.resources_released is False
    assert troop.count == 2
