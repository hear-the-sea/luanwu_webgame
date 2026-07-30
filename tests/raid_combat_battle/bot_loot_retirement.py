from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection
from django.utils import timezone

from battle.models import BattleReport
from battle.random_context import current_replay_metadata
from gameplay.models import BotProfile, BotSafetyMetricEvent, RaidRun
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.virtual_player_core import maintenance as virtual_player_maintenance
from gameplay.services.virtual_player_core.contracts import BotLootClampDecision
from gameplay.services.virtual_player_core.safety_metrics import H01_CALLBACK_ATTEMPT_METRIC, H01_RECOMMENDATION_METRIC


def _build_raid_case(django_user_model, *, suffix: str):
    attacker_user = django_user_model.objects.create_user(username=f"loot_retirement_attacker_{suffix}")
    defender_user = django_user_model.objects.create_user(username=f"loot_retirement_defender_{suffix}")
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    now = timezone.now()
    profile = BotProfile.objects.create(
        manor=defender,
        archetype=BotProfile.Archetype.RICH,
        state=BotProfile.State.ACTIVE,
        prestige_band="junior",
        growth_seed=7101,
        growth_stage=3,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=5),
        retire_at=now + timedelta(days=10),
        loot_budget_daily=500,
        last_planned_at=now,
    )
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        **current_replay_metadata(7101),
    )
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
        winner="attacker",
        starts_at=now,
        completed_at=now,
    )
    decision = BotLootClampDecision(
        resources={},
        bot_profile_id=profile.pk,
        bot_budget_exhausted=True,
        retirement_recommended=True,
    )
    return run, profile, report, decision, now


def _stub_successful_battle(monkeypatch, *, report: BattleReport, decision: BotLootClampDecision) -> None:
    def _prepare_run(run_pk: int, _now):
        locked_run = RaidRun.objects.select_for_update().select_related("attacker", "defender").get(pk=run_pk)
        locked_run.status = RaidRun.Status.BATTLING
        locked_run.save(update_fields=["status"])
        return locked_run

    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", _prepare_run)
    monkeypatch.setattr(
        combat_battle,
        "_lock_battle_manors",
        lambda attacker_id, defender_id: (
            combat_battle.Manor.objects.get(pk=attacker_id),
            combat_battle.Manor.objects.get(pk=defender_id),
        ),
    )
    monkeypatch.setattr(
        combat_battle,
        "capture_external_reconciliation_anchors",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(combat_battle, "audit_battle_replay_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_raid_loot_if_needed", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dismiss_marching_raids_if_protected", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dispatch_complete_raid_task", lambda *_args, **_kwargs: None)


@pytest.mark.django_db(transaction=True)
def test_raid_rollback_does_not_process_bot_retirement(monkeypatch, django_user_model):
    run, profile, report, decision, now = _build_raid_case(django_user_model, suffix="rollback")
    _stub_successful_battle(monkeypatch, report=report, decision=decision)
    retirement_calls: list[int] = []
    monkeypatch.setattr(
        combat_battle,
        "retire_virtual_player_if_unprotected",
        lambda profile_id, **_kwargs: retirement_calls.append(profile_id),
    )
    monkeypatch.setattr(
        combat_battle,
        "_apply_prestige_changes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("force raid rollback")),
    )

    with pytest.raises(RuntimeError, match="force raid rollback"):
        combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.refresh_from_db()
    assert run.status == RaidRun.Status.MARCHING
    assert profile.state == BotProfile.State.ACTIVE
    assert retirement_calls == []


@pytest.mark.django_db(transaction=True)
def test_outer_raid_transaction_rollback_discards_bot_retirement(monkeypatch, django_user_model):
    run, profile, report, decision, now = _build_raid_case(django_user_model, suffix="outer_rollback")
    _stub_successful_battle(monkeypatch, report=report, decision=decision)
    retirement_calls: list[int] = []
    monkeypatch.setattr(
        combat_battle,
        "retire_virtual_player_if_unprotected",
        lambda profile_id, **_kwargs: retirement_calls.append(profile_id),
    )

    with pytest.raises(RuntimeError, match="rollback caller transaction"):
        with combat_battle.transaction.atomic():
            combat_battle.process_raid_battle(run, now=now)
            assert retirement_calls == []
            raise RuntimeError("rollback caller transaction")

    run.refresh_from_db()
    profile.refresh_from_db()
    assert run.status == RaidRun.Status.MARCHING
    assert profile.state == BotProfile.State.ACTIVE
    assert retirement_calls == []


@pytest.mark.django_db(transaction=True)
def test_raid_processes_bot_retirement_only_after_commit(monkeypatch, django_user_model):
    run, profile, report, decision, now = _build_raid_case(django_user_model, suffix="commit")
    _stub_successful_battle(monkeypatch, report=report, decision=decision)
    original_retire = combat_battle.retire_virtual_player_if_unprotected
    observed: list[tuple[bool, str]] = []

    def _retire_after_commit(profile_id: int, **kwargs):
        observed.append(
            (
                connection.in_atomic_block,
                RaidRun.objects.values_list("status", flat=True).get(pk=run.pk),
            )
        )
        return original_retire(profile_id, **kwargs)

    monkeypatch.setattr(combat_battle, "retire_virtual_player_if_unprotected", _retire_after_commit)

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.refresh_from_db()
    assert observed == [(False, RaidRun.Status.RETURNING)]
    assert run.status == RaidRun.Status.RETURNING
    assert profile.state == BotProfile.State.RETIRED
    assert profile.maintenance_stopped_at == now


@pytest.mark.django_db
def test_repeated_bot_retirement_recommendation_is_idempotent(django_user_model):
    _run, profile, _report, decision, now = _build_raid_case(django_user_model, suffix="idempotent")

    combat_battle._process_bot_loot_retirement(decision, now=now)
    profile.refresh_from_db()
    first_updated_at = profile.updated_at

    combat_battle._process_bot_loot_retirement(decision, now=now + timedelta(minutes=1))
    profile.refresh_from_db()

    assert profile.state == BotProfile.State.RETIRED
    assert profile.maintenance_stopped_at == now
    assert profile.updated_at == first_updated_at


@pytest.mark.django_db
def test_repeated_protected_bot_retirement_recommendation_does_not_rewrite_profile(
    monkeypatch,
    django_user_model,
):
    _run, profile, _report, decision, now = _build_raid_case(django_user_model, suffix="protected_idempotent")
    profile.next_growth_at = now + timedelta(hours=2)
    profile.save(update_fields=["next_growth_at", "updated_at"])
    monkeypatch.setattr(
        virtual_player_maintenance,
        "is_virtual_profile_arena_protected",
        lambda **_kwargs: True,
    )

    combat_battle._process_bot_loot_retirement(decision, now=now)
    profile.refresh_from_db()
    first_updated_at = profile.updated_at
    assert profile.next_growth_at == now + timedelta(hours=1)

    combat_battle._process_bot_loot_retirement(decision, now=now)
    profile.refresh_from_db()

    assert profile.state == BotProfile.State.ACTIVE
    assert profile.next_growth_at == now + timedelta(hours=1)
    assert profile.updated_at == first_updated_at


@pytest.mark.django_db
def test_newer_protected_bot_retirement_recommendation_refreshes_deferral(
    monkeypatch,
    django_user_model,
):
    _run, profile, _report, decision, now = _build_raid_case(django_user_model, suffix="protected_newer")
    monkeypatch.setattr(
        virtual_player_maintenance,
        "is_virtual_profile_arena_protected",
        lambda **_kwargs: True,
    )

    combat_battle._process_bot_loot_retirement(decision, now=now)
    profile.refresh_from_db()
    first_updated_at = profile.updated_at

    newer_raid_time = now + timedelta(minutes=30)
    combat_battle._process_bot_loot_retirement(decision, now=newer_raid_time)
    profile.refresh_from_db()

    assert profile.state == BotProfile.State.ACTIVE
    assert profile.next_growth_at == newer_raid_time + timedelta(hours=1)
    assert profile.updated_at > first_updated_at


@pytest.mark.django_db(transaction=True)
def test_dropped_post_commit_registration_leaves_committed_raid_and_active_profile(
    monkeypatch,
    django_user_model,
):
    run, profile, report, decision, now = _build_raid_case(django_user_model, suffix="dropped_callback")
    _stub_successful_battle(monkeypatch, report=report, decision=decision)
    captured_callbacks = []
    monkeypatch.setattr(
        combat_battle.transaction,
        "on_commit",
        lambda callback: captured_callbacks.append(callback),
    )

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.refresh_from_db()
    assert run.status == RaidRun.Status.RETURNING
    assert run.is_attacker_victory is True
    assert run.battle_report_id == report.pk
    assert profile.state == BotProfile.State.ACTIVE
    assert len(captured_callbacks) == 1
    assert (
        BotSafetyMetricEvent.objects.filter(
            metric_name=H01_RECOMMENDATION_METRIC,
        ).count()
        == 1
    )
    assert not BotSafetyMetricEvent.objects.filter(
        metric_name=H01_CALLBACK_ATTEMPT_METRIC,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_retirement_programming_error_bubbles_after_committed_raid_cleanup_and_dispatch(
    monkeypatch,
    django_user_model,
):
    run, profile, report, decision, now = _build_raid_case(django_user_model, suffix="programming_error")
    _stub_successful_battle(monkeypatch, report=report, decision=decision)
    cleanup_calls: list[int] = []
    dispatch_calls: list[int] = []
    monkeypatch.setattr(
        combat_battle,
        "retire_virtual_player_if_unprotected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken retirement contract")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dismiss_marching_raids_if_protected",
        lambda manor: cleanup_calls.append(int(manor.pk)),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dispatch_complete_raid_task",
        lambda current_run, **_kwargs: dispatch_calls.append(int(current_run.pk)),
    )

    with pytest.raises(AssertionError, match="broken retirement contract"):
        combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.refresh_from_db()
    assert run.status == RaidRun.Status.RETURNING
    assert profile.state == BotProfile.State.ACTIVE
    assert cleanup_calls == [run.defender_id]
    assert dispatch_calls == [run.pk]


@pytest.mark.django_db(transaction=True)
def test_retirement_infrastructure_failure_does_not_rollback_committed_raid(
    monkeypatch,
    django_user_model,
    caplog,
):
    run, profile, report, decision, now = _build_raid_case(django_user_model, suffix="infra_failure")
    _stub_successful_battle(monkeypatch, report=report, decision=decision)
    monkeypatch.setattr(
        combat_battle,
        "retire_virtual_player_if_unprotected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("profile store unavailable")),
    )

    with caplog.at_level("WARNING", logger=combat_battle.logger.name):
        combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.refresh_from_db()
    assert run.status == RaidRun.Status.RETURNING
    assert run.is_attacker_victory is True
    assert run.battle_report_id == report.pk
    assert profile.state == BotProfile.State.ACTIVE
    assert any(
        getattr(record, "component", None) == "virtual_player_loot_retirement" and getattr(record, "degraded", False)
        for record in caplog.records
    )
