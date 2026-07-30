from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.exceptions import MessageError
from gameplay.models import RaidRun
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.virtual_player_core.contracts import BotLootClampDecision
from tests.raid_combat_battle.support import build_run


def test_process_raid_battle_known_post_commit_failures_degrade_and_continue(monkeypatch, caplog):
    now = timezone.now()
    attacker = SimpleNamespace(id=1, location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(id=2, location_display="塞北", display_name="防守方")
    saved = {"count": 0}
    dispatched = {"count": 0}
    run = build_run(run_id=7, attacker=attacker, defender=defender, save_counter=saved)
    report = SimpleNamespace(winner="attacker")

    monkeypatch.setattr(combat_battle.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_battle, "capture_external_reconciliation_anchors", lambda *_args: {})
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_apply_raid_loot_if_needed",
        lambda *_args, **_kwargs: BotLootClampDecision(resources={}),
    )
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_send_raid_battle_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MessageError("messages down")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dismiss_marching_raids_if_protected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("redis down")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dispatch_complete_raid_task",
        lambda *_args, **_kwargs: dispatched.__setitem__("count", dispatched["count"] + 1),
    )

    with caplog.at_level("WARNING", logger=combat_battle.logger.name):
        combat_battle.process_raid_battle(run, now=now)

    assert run.status == RaidRun.Status.RETURNING
    assert saved["count"] == 1
    assert dispatched["count"] == 1
    degraded_components = {
        getattr(record, "component", None) for record in caplog.records if getattr(record, "degraded", False)
    }
    assert degraded_components == {"raid_battle_message", "raid_protection_cleanup"}


def test_process_raid_battle_cleanup_runtime_marker_error_bubbles_after_dispatch(monkeypatch):
    now = timezone.now()
    attacker = SimpleNamespace(id=1, location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(id=2, location_display="塞北", display_name="防守方")
    saved = {"count": 0}
    dispatched = {"count": 0}
    run = build_run(run_id=19, attacker=attacker, defender=defender, save_counter=saved)
    report = SimpleNamespace(winner="attacker")

    monkeypatch.setattr(combat_battle.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_battle, "capture_external_reconciliation_anchors", lambda *_args: {})
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_apply_raid_loot_if_needed",
        lambda *_args, **_kwargs: BotLootClampDecision(resources={}),
    )
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_dismiss_marching_raids_if_protected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("redis down")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dispatch_complete_raid_task",
        lambda *_args, **_kwargs: dispatched.__setitem__("count", dispatched["count"] + 1),
    )

    with pytest.raises(RuntimeError, match="redis down"):
        combat_battle.process_raid_battle(run, now=now)

    assert run.status == RaidRun.Status.RETURNING
    assert saved["count"] == 1
    assert dispatched["count"] == 1


def test_process_raid_battle_message_programming_error_bubbles_after_dispatch(monkeypatch, caplog):
    now = timezone.now()
    attacker = SimpleNamespace(id=1, location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(id=2, location_display="塞北", display_name="防守方")
    saved = {"count": 0}
    dismissed = {"count": 0}
    dispatched = {"count": 0}
    run = build_run(run_id=17, attacker=attacker, defender=defender, save_counter=saved)
    report = SimpleNamespace(winner="attacker")

    monkeypatch.setattr(combat_battle.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_battle, "capture_external_reconciliation_anchors", lambda *_args: {})
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_apply_raid_loot_if_needed",
        lambda *_args, **_kwargs: BotLootClampDecision(resources={}),
    )
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_send_raid_battle_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken raid message contract")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dismiss_marching_raids_if_protected",
        lambda *_args, **_kwargs: dismissed.__setitem__("count", dismissed["count"] + 1),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dispatch_complete_raid_task",
        lambda *_args, **_kwargs: dispatched.__setitem__("count", dispatched["count"] + 1),
    )

    with pytest.raises(AssertionError, match="broken raid message contract"):
        combat_battle.process_raid_battle(run, now=now)

    assert run.status == RaidRun.Status.RETURNING
    assert saved["count"] == 1
    assert dismissed["count"] == 1
    assert dispatched["count"] == 1
    assert [record for record in caplog.records if getattr(record, "degraded", False)] == []


def test_process_raid_battle_cleanup_programming_error_bubbles_after_dispatch(monkeypatch, caplog):
    now = timezone.now()
    attacker = SimpleNamespace(id=1, location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(id=2, location_display="塞北", display_name="防守方")
    saved = {"count": 0}
    dispatched = {"count": 0}
    run = build_run(run_id=18, attacker=attacker, defender=defender, save_counter=saved)
    report = SimpleNamespace(winner="attacker")

    monkeypatch.setattr(combat_battle.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_battle, "capture_external_reconciliation_anchors", lambda *_args: {})
    monkeypatch.setattr(combat_battle, "_execute_raid_battle", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_apply_raid_loot_if_needed",
        lambda *_args, **_kwargs: BotLootClampDecision(resources={}),
    )
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_battle,
        "_dismiss_marching_raids_if_protected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken raid cleanup contract")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dispatch_complete_raid_task",
        lambda *_args, **_kwargs: dispatched.__setitem__("count", dispatched["count"] + 1),
    )

    with pytest.raises(AssertionError, match="broken raid cleanup contract"):
        combat_battle.process_raid_battle(run, now=now)

    assert run.status == RaidRun.Status.RETURNING
    assert saved["count"] == 1
    assert dispatched["count"] == 1
    assert [record for record in caplog.records if getattr(record, "degraded", False)] == []


def test_process_raid_battle_rechecks_defender_protection_before_fight(monkeypatch):
    now = timezone.now()
    attacker = SimpleNamespace(id=1, location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(id=2, location_display="塞北", display_name="防守方")
    run = build_run(run_id=8, attacker=attacker, defender=defender)
    dispatched = {"count": 0}
    retreated = {}

    monkeypatch.setattr(combat_battle.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(combat_battle, "_prepare_run_for_battle", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(combat_battle, "_lock_battle_manors", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(
        combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: "对方处于战败保护期"
    )
    monkeypatch.setattr(
        combat_battle,
        "_retreat_raid_run_due_to_blocked_target",
        lambda current_run, *, now=None, reason: retreated.update(
            {"run_id": current_run.id, "now": now, "reason": reason}
        ),
    )
    monkeypatch.setattr(
        combat_battle,
        "_execute_raid_battle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("battle should not execute")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_send_raid_battle_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("battle message should not send")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dismiss_marching_raids_if_protected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dismiss should not run")),
    )
    monkeypatch.setattr(
        combat_battle,
        "_dispatch_complete_raid_task",
        lambda *_args, **_kwargs: dispatched.__setitem__("count", dispatched["count"] + 1),
    )

    combat_battle.process_raid_battle(run, now=now)

    assert retreated == {"run_id": 8, "now": now, "reason": "对方处于战败保护期"}
    assert dispatched["count"] == 1
