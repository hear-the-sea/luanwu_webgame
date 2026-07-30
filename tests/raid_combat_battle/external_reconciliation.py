from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.models import BattleReport
from battle.random_context import current_replay_metadata
from gameplay.models import BotExternalStrengthReconciliation, BotProfile, RaidRun
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.virtual_player_core import external_reconciliation
from gameplay.services.virtual_player_core.contracts import BotLootClampDecision


def _build_v2_raid_case(django_user_model, *, suffix: str):
    now = timezone.now()
    attacker = ensure_manor(django_user_model.objects.create_user(username=f"raid_reconcile_attacker_{suffix}"))
    defender = ensure_manor(django_user_model.objects.create_user(username=f"raid_reconcile_defender_{suffix}"))
    defender.region = "north"
    defender.prestige = 100
    defender.save(update_fields=["region", "prestige"])
    profile = BotProfile.objects.create(
        manor=defender,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=721_001,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        engine_version=2,
        rng_version=1,
        plan_schema_version=1,
        policy_version=1,
        policy_checksum="b" * 64,
        development_profile={},
        last_strength_increase_at=now - timedelta(days=1),
        v2_enrolled_at=now - timedelta(days=1),
    )
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        travel_time=60,
        battle_at=now,
        return_at=now + timedelta(minutes=1),
        **current_replay_metadata(721_001),
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
    return run, profile, report, now


def _stub_successful_raid(monkeypatch, *, report: BattleReport) -> None:
    def _prepare_run(run_pk: int, _now):
        locked_run = RaidRun.objects.select_for_update().select_related("attacker", "defender").get(pk=run_pk)
        locked_run.status = RaidRun.Status.BATTLING
        locked_run.save(update_fields=["status"])
        return locked_run

    def _increase_defender_prestige(locked_run: RaidRun, _victory: bool) -> None:
        locked_run.defender.prestige += 1
        locked_run.defender.save(update_fields=["prestige"])

    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", _prepare_run)
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args: report)
    monkeypatch.setattr(combat_battle, "audit_battle_replay_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args: None)
    monkeypatch.setattr(
        combat_battle,
        "_apply_raid_loot_if_needed",
        lambda *_args, **_kwargs: BotLootClampDecision(resources={}),
    )
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", _increase_defender_prestige)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args: None)
    monkeypatch.setattr(combat_battle, "_dismiss_marching_raids_if_protected", lambda *_args: None)
    monkeypatch.setattr(combat_battle, "_dispatch_complete_raid_task", lambda *_args, **_kwargs: None)


@pytest.mark.django_db(transaction=True)
def test_raid_commits_bot_side_reconciliation_with_pre_change_anchor(
    monkeypatch,
    django_user_model,
) -> None:
    run, profile, report, now = _build_v2_raid_case(
        django_user_model,
        suffix="commit",
    )
    _stub_successful_raid(monkeypatch, report=report)
    queued: list[int] = []
    monkeypatch.setattr(
        external_reconciliation,
        "_queue_external_reconciliation",
        lambda reconciliation_id: queued.append(reconciliation_id) or True,
    )

    combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.manor.refresh_from_db()
    reconciliation = BotExternalStrengthReconciliation.objects.get()
    assert run.status == RaidRun.Status.RETURNING
    assert profile.manor.prestige == 101
    assert reconciliation.profile_id == profile.id
    assert reconciliation.domain_event_kind == "raid_result"
    assert reconciliation.domain_event_id == f"{run.id}:defender"
    assert reconciliation.origin_committed_at == now
    assert reconciliation.pre_strength_summary["components"]["prestige"] == 100
    assert reconciliation.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert queued == [reconciliation.id]


@pytest.mark.django_db(transaction=True)
def test_raid_rollback_discards_domain_change_and_reconciliation_intent(
    monkeypatch,
    django_user_model,
) -> None:
    run, profile, report, now = _build_v2_raid_case(
        django_user_model,
        suffix="rollback",
    )
    _stub_successful_raid(monkeypatch, report=report)
    queued: list[int] = []
    monkeypatch.setattr(
        external_reconciliation,
        "_queue_external_reconciliation",
        lambda reconciliation_id: queued.append(reconciliation_id) or True,
    )
    persist_intents = combat_battle._persist_raid_external_reconciliation_intents

    def _persist_then_fail(*args, **kwargs) -> None:
        persist_intents(*args, **kwargs)
        raise RuntimeError("force raid reconciliation rollback")

    monkeypatch.setattr(
        combat_battle,
        "_persist_raid_external_reconciliation_intents",
        _persist_then_fail,
    )

    with pytest.raises(RuntimeError, match="force raid reconciliation rollback"):
        combat_battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    profile.manor.refresh_from_db()
    assert run.status == RaidRun.Status.MARCHING
    assert run.battle_report_id is None
    assert profile.manor.prestige == 100
    assert not BotExternalStrengthReconciliation.objects.exists()
    assert queued == []
