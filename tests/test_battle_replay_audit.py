from __future__ import annotations

import logging
from types import SimpleNamespace

from battle.replay_audit import audit_battle_replay_metadata


def test_battle_replay_mismatch_emits_structured_audit_event(caplog):
    activity = SimpleNamespace(
        pk=17,
        base_seed=101,
        rng_version=1,
        battle_engine_version="2",
    )
    report = SimpleNamespace(
        pk=29,
        seed=202,
        rng_version=1,
        battle_engine_version="2",
    )
    logger = logging.getLogger("tests.battle.replay_audit")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        matched = audit_battle_replay_metadata(
            activity,
            report,
            logger=logger,
            activity_kind="arena_match",
        )

    assert matched is False
    record = next(record for record in caplog.records if getattr(record, "event", None) == "battle_replay_mismatch")
    assert record.activity_kind == "arena_match"
    assert record.activity_id == 17
    assert record.report_id == 29
    assert record.expected_replay_metadata == {
        "base_seed": 101,
        "rng_version": 1,
        "battle_engine_version": "2",
    }
    assert record.actual_replay_metadata["base_seed"] == 202


def test_battle_replay_match_is_silent(caplog):
    activity = SimpleNamespace(pk=17, base_seed=101, rng_version=1, battle_engine_version="2")
    report = SimpleNamespace(pk=29, seed=101, rng_version=1, battle_engine_version="2")
    logger = logging.getLogger("tests.battle.replay_audit.match")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        matched = audit_battle_replay_metadata(
            activity,
            report,
            logger=logger,
            activity_kind="raid_run",
        )

    assert matched is True
    assert not caplog.records
