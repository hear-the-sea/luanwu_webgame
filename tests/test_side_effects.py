import logging

import pytest

from core.utils import side_effects


def test_after_commit_best_effort_runs_only_after_commit(monkeypatch):
    calls: list[str] = []
    callbacks: list[object] = []

    monkeypatch.setattr(side_effects.transaction, "on_commit", lambda callback: callbacks.append(callback))

    side_effects.schedule_best_effort_after_commit(
        lambda: calls.append("ran"),
        logger=logging.getLogger("tests.side_effects"),
        log_message="follow-up failed",
    )
    assert calls == []
    assert len(callbacks) == 1

    callbacks[0]()
    assert calls == ["ran"]


def test_after_commit_best_effort_swallows_expected_infra_errors(caplog, monkeypatch):
    degraded_components: list[str] = []
    callbacks: list[object] = []

    def _boom():
        raise ConnectionError("broker down")

    monkeypatch.setattr(side_effects, "increment_degraded_counter", degraded_components.append)
    monkeypatch.setattr(side_effects.transaction, "on_commit", lambda callback: callbacks.append(callback))

    with caplog.at_level(logging.WARNING, logger="tests.side_effects"):
        side_effects.schedule_best_effort_after_commit(
            _boom,
            logger=logging.getLogger("tests.side_effects"),
            log_message="follow-up failed",
            degraded_component="test_followup",
        )
        callbacks[0]()

    assert any(
        record.name == "tests.side_effects" and "follow-up failed" in record.getMessage() for record in caplog.records
    )
    assert degraded_components == ["test_followup"]


def test_after_commit_best_effort_reraises_unexpected_errors(monkeypatch):
    callbacks: list[object] = []

    monkeypatch.setattr(side_effects.transaction, "on_commit", lambda callback: callbacks.append(callback))

    side_effects.schedule_best_effort_after_commit(
        lambda: (_ for _ in ()).throw(RuntimeError("broken callback")),
        logger=logging.getLogger("tests.side_effects"),
        log_message="follow-up failed",
    )

    with pytest.raises(RuntimeError, match="broken callback"):
        callbacks[0]()
