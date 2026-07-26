from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import GuildValidationError
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from guilds.models import Guild, GuildRaidRun, GuildTroopStorage
from tests.guild_pvp_service.support import (
    create_guest,
    create_guild_with_leader,
    create_template,
    seed_attacker_lineup,
)


def test_lock_guild_pair_orders_select_for_update_by_pk_and_preserves_roles_without_active_filter_by_default():
    from guilds.services.guild_raids import _lock_guild_pair

    filter_kwargs: dict[str, object] = {}
    ordered_fields: tuple[str, ...] = ()

    class _FakeGuild:
        def __init__(self, pk: int):
            self.pk = pk

    locked_guilds = [_FakeGuild(2), _FakeGuild(5)]

    class _FakeLockedQuerySet:
        def filter(self, **kwargs):
            nonlocal filter_kwargs
            filter_kwargs = kwargs
            return self

        def order_by(self, *fields):
            nonlocal ordered_fields
            ordered_fields = fields
            return locked_guilds

    class _FakeGuildManager:
        def select_for_update(self):
            return _FakeLockedQuerySet()

    class _FakeGuildModel:
        objects = _FakeGuildManager()

    attacker, defender = _lock_guild_pair(
        attacker_guild_id=5,
        defender_guild_id=2,
        guild_model=_FakeGuildModel,
    )

    assert filter_kwargs == {"pk__in": [2, 5]}
    assert ordered_fields == ("pk",)
    assert attacker.pk == 5
    assert defender.pk == 2


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_requires_active_guild_pair_lock(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "需激活")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "需激活守方")
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_require_active_tpl"),
        name="激活门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)
    helper_calls: list[dict[str, object]] = []

    class _StopStartRaid(RuntimeError):
        pass

    def _fake_lock_guild_pair(**kwargs):
        helper_calls.append(kwargs)
        raise _StopStartRaid

    monkeypatch.setattr("guilds.services.guild_raids.refresh_due_guild_raids", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("guilds.services.guild_raids._lock_guild_pair", _fake_lock_guild_pair)

    from guilds.services.guild_raids import start_guild_raid

    with pytest.raises(_StopStartRaid):
        start_guild_raid(
            guild=attacker_guild,
            defender_guild=defender_guild,
            operator=leader.user,
            pool_entry_ids=[pool_entry_id],
            troop_loadout={},
        )

    assert helper_calls == [
        {
            "attacker_guild_id": attacker_guild.pk,
            "defender_guild_id": defender_guild.pk,
            "require_active": True,
        }
    ]


@pytest.mark.django_db
def test_guild_raid_run_status_choices():
    statuses = {choice for choice, _label in GuildRaidRun.Status.choices}
    assert statuses == {"marching", "battling", "returning", "completed", "retreated"}


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_generates_guest_snapshots_and_travel_time(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "发起方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "防守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_start_tpl"),
        name="进攻门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)

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
@override_settings(GAME_TIME_MULTIPLIER=1)
def test_start_guild_raid_accepts_troops_at_exact_guest_capacity(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "容量刚好攻方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "容量刚好守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_exact_capacity_tpl"),
        name="容量门客",
    )
    guest.agility = 160
    guest.save(update_fields=["agility"])
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)
    troop_template = TroopTemplate.objects.create(key="guild_pvp_exact_guard", name="容量护院")
    storage = GuildTroopStorage.objects.create(guild=attacker_guild, troop_template=troop_template, count=200)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda _run: None)
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_warning_messages", lambda _run: None)

    from guilds.services.guild_raids import start_guild_raid

    run = start_guild_raid(
        guild=attacker_guild,
        defender_guild=defender_guild,
        operator=leader.user,
        pool_entry_ids=[pool_entry_id],
        troop_loadout={troop_template.key: 200},
    )

    storage.refresh_from_db()
    assert guest.troop_capacity == 200
    assert run.troop_loadout == {troop_template.key: 200}
    assert run.travel_time == 29520
    assert int((run.battle_at - run.started_at).total_seconds()) == 29520
    assert int((run.return_at - run.started_at).total_seconds()) == 59040
    assert storage.count == 0


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_rejects_one_troop_over_capacity_without_deducting_storage(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "容量超限攻方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "容量超限守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_over_capacity_tpl"),
        name="超限门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)
    troop_template = TroopTemplate.objects.create(key="guild_pvp_over_guard", name="超限护院")
    storage = GuildTroopStorage.objects.create(guild=attacker_guild, troop_template=troop_template, count=201)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda _run: None)

    from guilds.services.guild_raids import start_guild_raid

    with pytest.raises(GuildValidationError, match="总带兵上限为200，实际兵力为201"):
        start_guild_raid(
            guild=attacker_guild,
            defender_guild=defender_guild,
            operator=leader.user,
            pool_entry_ids=[pool_entry_id],
            troop_loadout={troop_template.key: 201},
        )

    storage.refresh_from_db()
    attacker_guild.refresh_from_db()
    assert storage.count == 201
    assert attacker_guild.silver == 50000
    assert not GuildRaidRun.objects.filter(attacker_guild=attacker_guild).exists()


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_snapshots_attacker_troop_tech_levels(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "科技快照攻方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "科技快照守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_start_tech_snapshot_tpl"),
        name="进攻门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)

    monkeypatch.setattr("guilds.services.guild_raids.calculate_guild_raid_travel_time", lambda *_a, **_k: 120)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda _run: None)
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_warning_messages", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "guilds.services.guild_raids.build_guild_troop_tech_levels",
        lambda _guild: {"gong_attack": 2, "gong_hp": 1},
        raising=False,
    )

    from guilds.services.guild_raids import start_guild_raid

    run = start_guild_raid(
        guild=attacker_guild,
        defender_guild=defender_guild,
        operator=leader.user,
        pool_entry_ids=[pool_entry_id],
        troop_loadout={},
    )

    assert run.attacker_troop_tech_snapshot == {"gong_attack": 2, "gong_hp": 1}


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_refreshes_due_runs_before_locking_guild_pair(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "先刷新")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "后锁帮")
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_refresh_before_lock_tpl"),
        name="顺序门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)
    events: list[str] = []

    class _StopStartRaid(RuntimeError):
        pass

    def _fake_refresh_due_guild_raids(*_args, **_kwargs):
        events.append("refresh_due_guild_raids")
        raise _StopStartRaid

    def _fake_lock_guild_pair(*_args, **_kwargs):
        events.append("lock_guild_pair")
        return attacker_guild, defender_guild

    monkeypatch.setattr("guilds.services.guild_raids.refresh_due_guild_raids", _fake_refresh_due_guild_raids)
    monkeypatch.setattr("guilds.services.guild_raids._lock_guild_pair", _fake_lock_guild_pair)
    monkeypatch.setattr(
        "guilds.services.guild_raids.reset_guild_pvp_counters_if_needed", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.can_attack_guild",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(
        "guilds.services.guild_raids.lock_manage_member",
        lambda *_args, **_kwargs: leader,
    )

    from guilds.services.guild_raids import start_guild_raid

    with pytest.raises(_StopStartRaid):
        start_guild_raid(
            guild=attacker_guild,
            defender_guild=defender_guild,
            operator=leader.user,
            pool_entry_ids=[pool_entry_id],
            troop_loadout={},
        )

    assert events == ["refresh_due_guild_raids"]


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_clears_attacker_defeat_protection_on_success(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "清保护")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "目标帮")
    attacker_guild.silver = 50000
    attacker_guild.defeat_protection_until = timezone.now() + timedelta(hours=2)
    attacker_guild.save(update_fields=["silver", "defeat_protection_until"])
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_clear_protection_tpl"),
        name="进攻门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)

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
def test_start_guild_raid_keeps_due_battle_processing_when_new_launch_validation_fails(django_user_model, monkeypatch):
    from battle.models import BattleReport
    from guilds.services.guild_raids import start_guild_raid

    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "事务攻方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "事务守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_txn_rollback_tpl"),
        name="事务门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=attacker_member, guest=attacker_guest)
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
    monkeypatch.setattr("guilds.services.guild_raids.calculate_battle_salvage", lambda *_args, **_kwargs: (0, {}))
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_report_messages", lambda *_args, **_kwargs: None)

    with pytest.raises(GuildValidationError, match="当前已有帮会对战队伍出征中"):
        start_guild_raid(
            guild=attacker_guild,
            defender_guild=defender_guild,
            operator=attacker_member.user,
            pool_entry_ids=[pool_entry_id],
            troop_loadout={},
        )

    due_run.refresh_from_db()
    assert due_run.status == GuildRaidRun.Status.RETURNING
    assert due_run.battle_report_id == report.id


@pytest.mark.django_db(transaction=True)
def test_request_retreat_keeps_overdue_raid_refresh_after_validation_error(django_user_model, monkeypatch):
    attacker_guild, attacker_member, _attacker_manor = create_guild_with_leader(
        django_user_model,
        "过期撤回攻方",
    )
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(
        django_user_model,
        "过期撤回守方",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=0,
        guest_ids=[],
        guest_snapshots=[],
        troop_loadout={},
        travel_time=60,
        battle_at=now - timedelta(seconds=1),
        return_at=now + timedelta(seconds=59),
    )

    def _refresh_overdue(_guild, *, now):
        GuildRaidRun.objects.filter(pk=run.pk).update(status=GuildRaidRun.Status.RETURNING)

    monkeypatch.setattr("guilds.services.guild_raids.refresh_due_guild_raids", _refresh_overdue)

    from guilds.services.guild_raids import request_retreat

    with pytest.raises(GuildValidationError, match="当前出征不可撤回"):
        request_retreat(run=run, operator=attacker_member.user)

    run.refresh_from_db()
    assert run.status == GuildRaidRun.Status.RETURNING


@pytest.mark.django_db
def test_get_guild_pvp_page_context_uses_supplied_now_for_counter_projection(django_user_model):
    guild, member, _manor = create_guild_with_leader(django_user_model, "计数投影")
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
def test_prepare_guild_pvp_read_state_does_not_process_due_incoming_marching_run(django_user_model, monkeypatch):
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "读侧守方")
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "读侧攻方")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_read_state_tpl"),
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

    assert processed_run_ids == []
    assert GuildRaidRun.objects.filter(pk=due_run.pk, status=GuildRaidRun.Status.MARCHING).exists()


@pytest.mark.django_db
def test_process_due_guild_pvp_activity_processes_due_incoming_marching_run(django_user_model, monkeypatch):
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "显式守方")
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "显式攻方")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_command_state_tpl"),
        name="显式门客",
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

    from guilds.services.guild_raids import process_due_guild_pvp_activity

    processed_count = process_due_guild_pvp_activity(defender_guild, now=now)

    assert processed_count == 1
    assert processed_run_ids == [due_run.id]


@pytest.mark.django_db(transaction=True)
def test_start_guild_raid_defers_warning_messages_until_after_commit(django_user_model, monkeypatch):
    attacker_guild, leader, _attacker_manor = create_guild_with_leader(django_user_model, "延后预警攻方")
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(django_user_model, "延后预警守方")
    attacker_guild.silver = 50000
    attacker_guild.save(update_fields=["silver"])
    guest = create_guest(
        manor=leader.user.manor,
        template=create_template("guild_pvp_warning_after_commit_tpl"),
        name="延后预警门客",
    )
    pool_entry_id = seed_attacker_lineup(guild=attacker_guild, leader=leader, guest=guest)

    callbacks: list[object] = []
    warning_run_ids: list[int] = []

    monkeypatch.setattr(
        "guilds.services.guild_raids.transaction.on_commit", lambda callback: callbacks.append(callback)
    )
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
        lambda run: warning_run_ids.append(run.id),
    )

    from guilds.services.guild_raids import start_guild_raid

    run = start_guild_raid(
        guild=attacker_guild,
        defender_guild=defender_guild,
        operator=leader.user,
        pool_entry_ids=[pool_entry_id],
        troop_loadout={},
    )

    assert warning_run_ids == []
    assert len(callbacks) == 1

    callbacks[0]()

    assert warning_run_ids == [run.id]


@pytest.mark.django_db
def test_get_guild_pvp_page_context_keeps_overdue_and_battling_incoming_runs_visible(django_user_model):
    defender_guild, defender_member, _defender_manor = create_guild_with_leader(django_user_model, "来袭守方")
    attacker_guild, attacker_member, attacker_manor = create_guild_with_leader(django_user_model, "来袭攻方")
    attacker_guest = create_guest(
        manor=attacker_manor,
        template=create_template("guild_pvp_incoming_visibility_tpl"),
        name="来袭门客",
    )
    now = timezone.now()
    overdue_marching = GuildRaidRun.objects.create(
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
    battling = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.BATTLING,
        selected_guest_count=1,
        guest_ids=[attacker_guest.id],
        guest_snapshots=build_guest_battle_snapshots([attacker_guest], include_identity=True),
        troop_loadout={},
        travel_time=300,
        battle_at=now - timedelta(seconds=30),
        return_at=now + timedelta(seconds=270),
    )

    from guilds.services.guild_pvp_queries import get_guild_pvp_page_context

    context = get_guild_pvp_page_context(defender_member, now=now)

    assert {run.run.id for run in context["incoming_runs"]} == {overdue_marching.id, battling.id}
