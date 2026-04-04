from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.models import BattleReport
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember, GuildRaidRun


def _create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def _create_guild_with_leader(django_user_model, suffix: str) -> tuple[Guild, GuildMember, object]:
    leader, manor = _create_user_with_manor(django_user_model, f"guild_pvp_task_{suffix}")
    guild = Guild.objects.create(name=f"任{suffix}"[:12], founder=leader, is_active=True)
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


@pytest.mark.django_db
def test_complete_guild_raid_task_reschedules_marching_run_until_battle_at(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_raid_task

    now = timezone.now()
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "攻方")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "守方")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_task_tpl"),
        name="任务门客",
    )
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
        battle_at=now + timedelta(seconds=20),
        return_at=now + timedelta(seconds=40),
    )

    dispatched: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.safe_apply_async_with_dedup",
        lambda *args, **kwargs: dispatched.append((args, kwargs)) or True,
    )

    assert complete_guild_raid_task.run(run.id) == "rescheduled"
    assert dispatched
    _args, kwargs = dispatched[-1]
    assert kwargs["args"] == [run.id]
    assert kwargs["countdown"] == 20


@pytest.mark.django_db
def test_complete_guild_raid_task_processes_due_marching_run(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_raid_task

    now = timezone.now()
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "攻方")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "守方")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_task_process_tpl"),
        name="任务门客",
    )
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
        battle_at=now - timedelta(seconds=10),
        return_at=now + timedelta(seconds=290),
    )

    processed: list[tuple[int, object]] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.process_guild_raid_battle",
        lambda locked_run, now=None: processed.append((locked_run.id, now)) or True,
    )

    assert complete_guild_raid_task.run(run.id) == "completed"
    assert processed == [(run.id, now)]


@pytest.mark.django_db
def test_complete_guild_raid_task_finalizes_due_returning_run(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_raid_task

    now = timezone.now()
    attacker_guild, attacker_member, attacker_manor = _create_guild_with_leader(django_user_model, "回攻")
    defender_guild, _defender_member, _defender_manor = _create_guild_with_leader(django_user_model, "回守")
    attacker_guest = _create_guest(
        manor=attacker_manor,
        template=_create_template("guild_pvp_task_finalize_tpl"),
        name="返程门客",
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
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        travel_time=300,
        battle_report=report,
        battle_at=now - timedelta(seconds=300),
        return_at=now - timedelta(seconds=1),
    )

    finalized: list[tuple[int, object]] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.finalize_guild_raid",
        lambda locked_run, now=None: finalized.append((locked_run.id, now)) or True,
    )

    assert complete_guild_raid_task.run(run.id) == "completed"
    assert finalized == [(run.id, now)]
