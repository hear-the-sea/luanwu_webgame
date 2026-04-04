from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from battle import execution as battle_execution
from battle.combatants_pkg import cache as guest_template_cache
from battle.models import BattleReport, TroopTemplate
from core.exceptions import GuildValidationError
from gameplay.models import Message
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTechnology, GuildTroopStorage
from guilds.services import hero_pool as hero_pool_service


def _create_guild_and_leader(django_user_model, suffix: str) -> tuple[Guild, GuildMember]:
    user = django_user_model.objects.create_user(username=f"guild_mission_{suffix}", password="pass12345")
    guild = Guild.objects.create(name=f"帮会任务{suffix}", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    return guild, member


def _create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def _create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def _create_guest(*, manor, template: GuestTemplate, name: str, level: int = 20) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=level,
        force=120,
        intellect=85,
        defense_stat=100,
        agility=90,
        luck=60,
    )


class TestGuildMissionModelConstraints:
    @pytest.mark.django_db
    def test_guild_mission_run_exposes_status_constants(self):
        assert GuildMissionRun.Status.ACTIVE == "active"
        assert GuildMissionRun.Status.COMPLETED == "completed"
        assert GuildMissionRun.Status.RETREATED == "retreated"

    @pytest.mark.django_db
    def test_guild_mission_run_allows_only_one_active_run_per_guild(self, django_user_model):
        guild, leader = _create_guild_and_leader(django_user_model, "unique_run")
        template = GuildMissionTemplate.objects.create(
            key="model_unique_run",
            name="模型唯一运行任务",
            description="smoke",
            difficulty="junior",
            task_type="guest",
            base_duration_seconds=300,
            ruby_reward=10,
            recommended_guest_count=1,
        )

        GuildMissionRun.objects.create(
            guild=guild,
            template=template,
            started_by=leader,
            status="active",
            selected_guest_count=1,
            ruby_reward=10,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                GuildMissionRun.objects.create(
                    guild=guild,
                    template=template,
                    started_by=leader,
                    status="active",
                    selected_guest_count=1,
                    ruby_reward=10,
                )

    @pytest.mark.django_db
    def test_guild_troop_storage_is_unique_per_template(self, django_user_model):
        guild, _leader = _create_guild_and_leader(django_user_model, "unique_storage")
        troop_template = TroopTemplate.objects.create(key="guild_model_archer", name="模型弓兵")

        GuildTroopStorage.objects.create(
            guild=guild,
            troop_template=troop_template,
            count=100,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                GuildTroopStorage.objects.create(
                    guild=guild,
                    troop_template=troop_template,
                    count=50,
                )


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_snapshots_guests_and_deducts_troops(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_leader")
    guild = Guild.objects.create(name="帮会任务发起帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_task",
        name="巡山",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=2,
        allow_troops=True,
        is_active=True,
        sort_weight=1,
    )
    GuildTechnology.objects.create(guild=guild, tech_key="guild_dispatch_capacity", level=2, max_level=20)
    troop_template = TroopTemplate.objects.create(key="guild_launch_archer", name="任务弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest_a = _create_guest(manor=leader_manor, template=_create_template("guild_launch_tpl_a"), name="甲")
    guest_b = _create_guest(manor=leader_manor, template=_create_template("guild_launch_tpl_b"), name="乙")
    entry_a = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1).entry
    entry_b = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_b.id, slot_index=2).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_a.id)
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_b.id)

    scheduled_run_ids: list[int] = []
    monkeypatch.setattr(
        "guilds.services.guild_missions.schedule_guild_mission_completion",
        lambda run: scheduled_run_ids.append(run.id),
    )

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry_a.id, entry_b.id],
        troop_loadout={troop_template.key: 20},
    )

    assert run.status == "active"
    assert run.started_by_id == leader_member.id
    assert run.selected_guest_count == 2
    assert run.guest_ids == [guest_a.id, guest_b.id]
    assert len(run.guest_snapshots) == 2
    assert GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template).count == 30
    assert run.return_at is not None
    assert scheduled_run_ids == [run.id]


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_clamps_snapshot_hp_when_guest_current_hp_exceeds_max(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_hp_clamp_leader")
    guild = Guild.objects.create(name="帮会任务血量钳制帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_hp_clamp_task",
        name="血量钳制任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=2,
    )
    guest = _create_guest(
        manor=leader_manor,
        template=_create_template("guild_launch_hp_clamp_tpl"),
        name="钳制门客",
    )
    guest.current_hp = guest.max_hp + 5000
    guest.save(update_fields=["current_hp"])

    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr(
        "guilds.services.guild_missions.schedule_guild_mission_completion",
        lambda _run: None,
    )

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    assert len(run.guest_snapshots) == 1
    assert run.guest_snapshots[0]["current_hp"] == run.guest_snapshots[0]["max_hp"]


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_defers_completion_dispatch_until_commit(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_on_commit_leader")
    guild = Guild.objects.create(name="帮会任务提交后调度帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_on_commit_task",
        name="提交后调度",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=3,
    )
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_launch_on_commit_tpl"), name="甲")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    dispatch_calls: list[list[int]] = []
    monkeypatch.setattr(
        "guilds.services.guild_missions.safe_apply_async",
        lambda _task, *, args=None, **_kwargs: dispatch_calls.append(list(args or [])) or True,
    )

    from guilds.services import guild_missions as guild_mission_service

    with transaction.atomic():
        with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
            run = guild_mission_service.launch_guild_mission(
                guild=guild,
                operator=leader,
                template_key=template.key,
                pool_entry_ids=[entry.id],
                troop_loadout={},
            )
            assert dispatch_calls == []
    assert len(callbacks) == 1

    assert dispatch_calls == [[run.id]]


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_applies_global_time_multiplier_to_schedule(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_time_scale_leader")
    guild = Guild.objects.create(name="帮会任务时间倍率帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_time_scale_task",
        name="时间倍率任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=4,
    )
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_launch_time_scale_tpl"), name="甲")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    fixed_now = timezone.now()
    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: fixed_now)

    with override_settings(GAME_TIME_MULTIPLIER=5):
        run = guild_mission_service.launch_guild_mission(
            guild=guild,
            operator=leader,
            template_key=template.key,
            pool_entry_ids=[entry.id],
            troop_loadout={},
        )

    assert run.battle_at == fixed_now + timedelta(seconds=120)
    assert run.return_at == fixed_now + timedelta(seconds=120)


@pytest.mark.django_db
def test_schedule_guild_mission_completion_recomputes_countdown_after_commit(monkeypatch):
    from guilds.services import guild_missions as guild_mission_service

    now = timezone.now()
    run = SimpleNamespace(id=321, return_at=now + timedelta(seconds=30))
    callbacks: list[object] = []
    countdowns: list[int] = []

    monkeypatch.setattr(guild_mission_service.transaction, "on_commit", lambda callback: callbacks.append(callback))
    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: now)
    monkeypatch.setattr(
        guild_mission_service,
        "safe_apply_async",
        lambda _task, *, countdown=None, **_kwargs: countdowns.append(int(countdown or 0)) or True,
    )

    guild_mission_service.schedule_guild_mission_completion(run)

    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: now + timedelta(seconds=7))
    callbacks[0]()

    assert countdowns == [23]


@pytest.mark.django_db
def test_schedule_guild_mission_completion_finalizes_sync_when_due_dispatch_fails(monkeypatch):
    from guilds.services import guild_missions as guild_mission_service

    now = timezone.now()
    run = SimpleNamespace(id=322, return_at=now)
    callbacks: list[object] = []
    finalized: list[tuple[int, object]] = []

    monkeypatch.setattr(guild_mission_service.transaction, "on_commit", lambda callback: callbacks.append(callback))
    monkeypatch.setattr(guild_mission_service.timezone, "now", lambda: now)
    monkeypatch.setattr(guild_mission_service, "safe_apply_async", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        guild_mission_service,
        "finalize_guild_mission_run",
        lambda scheduled_run, now=None: finalized.append((scheduled_run.id, now)) or True,
    )

    guild_mission_service.schedule_guild_mission_completion(run)
    callbacks[0]()

    assert finalized == [(322, now)]


@pytest.mark.django_db(transaction=True)
def test_retreat_guild_mission_returns_all_troops_without_ruby_reward(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_retreat_leader")
    guild = Guild.objects.create(name="帮会任务撤回帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_task",
        name="撤回测试任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
        sort_weight=2,
    )
    troop_template = TroopTemplate.objects.create(key="guild_retreat_archer", name="撤回弓手")
    storage = GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_retreat_tpl"), name="撤回门客")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={troop_template.key: 20},
    )

    guild_mission_service.request_retreat(run=run, operator=leader)
    run.refresh_from_db()
    storage.refresh_from_db()

    assert run.status == "retreated"
    assert run.completed_at is not None
    assert storage.count == 50
    assert not guild.warehouse_items.filter(item_key="red_ruby").exists()


@pytest.mark.django_db(transaction=True)
def test_get_guild_mission_page_context_finalizes_overdue_active_run(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_context_finalize_leader")
    guild = Guild.objects.create(name="帮会任务读路径收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_context_finalize_task",
        name="读路径收口任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
    )
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_context_finalize_tpl"), name="收口门客")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )
    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": []}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=11,
    )
    GuildMissionRun.objects.filter(pk=run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(guild_mission_service, "execute_battle", lambda *args, **kwargs: report)

    context = guild_mission_service.get_guild_mission_page_context(leader_member)

    assert context["active_run"] is None
    run.refresh_from_db()
    assert run.status == GuildMissionRun.Status.COMPLETED
    assert run.completed_at is not None
    assert run.battle_report_id == report.id


@pytest.mark.django_db(transaction=True)
def test_launch_guild_mission_ignores_overdue_active_run_after_finalizing(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_overdue_leader")
    guild = Guild.objects.create(name="帮会任务发起收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_launch_overdue_task",
        name="发起收口任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
    )
    guest = _create_guest(
        manor=leader_manor, template=_create_template("guild_launch_overdue_tpl"), name="继续出征门客"
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    overdue_run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )
    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": []}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=12,
    )
    GuildMissionRun.objects.filter(pk=overdue_run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(guild_mission_service, "execute_battle", lambda *args, **kwargs: report)

    new_run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    overdue_run.refresh_from_db()
    assert overdue_run.status == GuildMissionRun.Status.COMPLETED
    assert new_run.status == GuildMissionRun.Status.ACTIVE
    assert GuildMissionRun.objects.filter(guild=guild, status=GuildMissionRun.Status.ACTIVE).count() == 1


@pytest.mark.django_db(transaction=True)
def test_request_retreat_finalizes_overdue_active_run_instead_of_refunding_all_troops(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_retreat_overdue_leader")
    guild = Guild.objects.create(name="帮会任务撤回收口帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_overdue_task",
        name="撤回收口任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key="guild_retreat_overdue_archer", name="撤回收口弓手")
    storage = GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = _create_guest(
        manor=leader_manor, template=_create_template("guild_retreat_overdue_tpl"), name="收口撤回门客"
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={troop_template.key: 20},
    )
    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={troop_template.key: 20},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": [{"key": troop_template.key, "lost": 8}]}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=13,
    )
    GuildMissionRun.objects.filter(pk=run.pk).update(return_at=timezone.now() - timedelta(seconds=1))
    monkeypatch.setattr(guild_mission_service, "execute_battle", lambda *args, **kwargs: report)

    with pytest.raises(GuildValidationError, match="当前任务不可撤回"):
        guild_mission_service.request_retreat(run=run, operator=leader)

    run.refresh_from_db()
    storage.refresh_from_db()

    assert run.status == GuildMissionRun.Status.COMPLETED
    assert storage.count == 42


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_returns_survivors_and_awards_ruby(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_finalize_leader")
    guild = Guild.objects.create(name="帮会任务结算帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuildMissionTemplate.objects.create(
        key="guild_finalize_task",
        name="剿匪",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=5,
        recommended_guest_count=1,
        allow_troops=True,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key="guild_finalize_archer", name="结算弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_finalize_tpl"), name="先锋")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={troop_template.key: 20},
    )

    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={troop_template.key: 20},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": [{"key": troop_template.key, "lost": 8}]}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=1,
    )
    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", lambda *args, **kwargs: report)

    now = timezone.now()
    guild_mission_service.finalize_guild_mission_run(run, now=now)
    run.refresh_from_db()

    storage = GuildTroopStorage.objects.get(guild=guild, troop_template=troop_template)
    assert run.status == "completed"
    assert run.completed_at == now
    assert run.battle_report_id == report.id
    assert storage.count == 42
    assert guild.warehouse_items.get(item_key="red_ruby").quantity == 5


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_sends_report_message_to_all_active_members(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_report_leader")
    admin, admin_manor = _create_user_with_manor(django_user_model, "guild_mission_report_admin")
    member, member_manor = _create_user_with_manor(django_user_model, "guild_mission_report_member")
    guild = Guild.objects.create(name="帮会任务战报群发帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    GuildMember.objects.create(guild=guild, user=admin, position="admin", is_active=True)
    GuildMember.objects.create(guild=guild, user=member, position="member", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_report_delivery_task",
        name="群发战报任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=1,
        allow_troops=False,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        is_active=True,
    )
    guest = _create_guest(manor=leader_manor, template=_create_template("guild_report_delivery_tpl"), name="先锋")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    monkeypatch.setattr("guilds.services.guild_missions.schedule_guild_mission_completion", lambda _run: None)

    from guilds.services import guild_missions as guild_mission_service

    run = guild_mission_service.launch_guild_mission(
        guild=guild,
        operator=leader,
        template_key=template.key,
        pool_entry_ids=[entry.id],
        troop_loadout={},
    )

    report = BattleReport.objects.create(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {"casualties": []}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now() - timedelta(seconds=5),
        completed_at=timezone.now(),
        seed=21,
    )
    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", lambda *args, **kwargs: report)

    assert guild_mission_service.finalize_guild_mission_run(run)

    messages = list(
        Message.objects.filter(
            kind=Message.Kind.BATTLE,
            title=f"{template.name} 战报",
            battle_report=report,
        ).order_by("manor_id")
    )

    assert [message.manor_id for message in messages] == sorted([leader_manor.id, admin_manor.id, member_manor.id])


@pytest.mark.django_db(transaction=True)
def test_finalize_guild_mission_forwards_expanded_battle_limits(django_user_model, monkeypatch):
    leader, leader_manor = _create_user_with_manor(django_user_model, "guild_mission_expand_limits_leader")
    guild = Guild.objects.create(name="公会任务扩展人数帮", founder=leader, is_active=True)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    enemy_templates = [_create_template(f"guild_expand_enemy_template_{i}") for i in range(7)]
    enemy_guests = [{"template_key": tpl.key, "level": 25} for tpl in enemy_templates]
    assert all("template_key" in entry for entry in enemy_guests)
    template = GuildMissionTemplate.objects.create(
        key="guild_expand_limits_task",
        name="人数上限测试",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=60,
        ruby_reward=0,
        recommended_guest_count=6,
        allow_troops=False,
        enemy_guests=enemy_guests,
        enemy_troops={"archer": 120},
        enemy_technology={"guest_level": 60, "guest_bonus": 0.15, "guest_skills": ["stratagem_burst"]},
        is_active=True,
    )

    guest_template = _create_template("guild_expand_limits_guest")
    guests = [_create_guest(manor=leader_manor, template=guest_template, name=f"门客{i}") for i in range(6)]
    guest_snapshots = build_guest_battle_snapshots(guests, include_identity=True)
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=leader_member,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=len(guests),
        ruby_reward=0,
        guest_ids=[guest.id for guest in guests],
        guest_snapshots=guest_snapshots,
        troop_loadout={},
    )

    guest_template_cache.clear_guest_template_cache()

    captured: dict[str, Any] = {}

    original_build_named = battle_execution.build_named_ai_guests

    def _wrapped_build_named_ai_guests(guest_keys, level):
        captured["raw_guest_keys"] = [dict(entry) if isinstance(entry, dict) else entry for entry in guest_keys]
        captured["enemy_level"] = level
        return original_build_named(guest_keys, level=level)

    monkeypatch.setattr(
        "battle.execution.build_named_ai_guests",
        _wrapped_build_named_ai_guests,
    )

    original_execute = battle_execution.execute_battle

    def _wrapped_execute_battle(manor, guests_arg, active_guests_arg, options):
        captured["len_guests"] = len(guests_arg)
        captured["len_active_guests"] = len(active_guests_arg)
        captured["options"] = options
        return original_execute(manor, guests_arg, active_guests_arg, options)

    monkeypatch.setattr("guilds.services.guild_missions.execute_battle", _wrapped_execute_battle)

    def _fake_execute_simulation(attacker_units, defender_units, options, config, rng, final_seed):
        captured["attacker_unit_count"] = len(attacker_units)
        captured["defender_unit_count"] = len(defender_units)
        simulation = SimpleNamespace(
            losses={"attacker": {"casualties": []}, "defender": {}},
            drops={},
            winner="attacker",
            rounds=[],
            starts_at=timezone.now(),
            completed_at=timezone.now(),
            seed=final_seed,
        )
        return simulation, options.opponent_name or config.get("name", "乱军试炼")

    monkeypatch.setattr("battle.execution._execute_simulation", _fake_execute_simulation)

    from guilds.services import guild_missions as guild_mission_service

    assert guild_mission_service.finalize_guild_mission_run(run)

    options = captured.get("options")
    assert options is not None
    assert captured.get("len_guests") == 6
    assert captured.get("len_active_guests") == 6
    assert captured["enemy_level"] == 60
    assert options.limit == 6
    assert options.defender_limit == 7
    assert captured["attacker_unit_count"] == 6
    assert captured["defender_unit_count"] == 7
    assert options.defender_setup["troop_loadout"] == {"archer": 120}
    assert options.defender_setup["technology"]["guest_level"] == 60
    assert len(captured["raw_guest_keys"]) == 7
    assert all(isinstance(entry, dict) for entry in captured["raw_guest_keys"])
    assert all("template_key" not in entry for entry in captured["raw_guest_keys"])
    assert [entry["key"] for entry in captured["raw_guest_keys"]] == [tpl.key for tpl in enemy_templates]
