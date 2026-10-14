from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import worker_liveness as supervision
from core.worker_liveness import RedisConsumerSupervisor, StallDetector


def test_stall_requires_timeout_and_consecutive_idle_observations():
    detector = StallDetector(0, 0, startup_grace=120, stall_seconds=600, failure_limit=3)
    assert not detector.observe(119, 100, False)
    assert not detector.observe(599, 100, False)
    assert not detector.observe(600, 100, False)
    assert not detector.observe(630, 100, False)
    assert detector.observe(660, 100, False)


@pytest.mark.parametrize("backlog,busy", [(0, False), (None, False), (100, True)])
def test_empty_queue_outage_or_long_task_restarts_the_observation_window(backlog, busy):
    detector = StallDetector(0, 0, failures=2)
    assert not detector.observe(1000, backlog, busy)
    assert detector.failures == 0
    assert not detector.observe(1030, 100, False)


def make_supervisor(pool_cache=None):
    pool = SimpleNamespace(_pool=SimpleNamespace(_cache=pool_cache))
    app = SimpleNamespace(conf={})
    return RedisConsumerSupervisor(SimpleNamespace(pool=pool, app=app))


def test_task_receipt_breaks_consecutive_failure_streak(monkeypatch):
    watchdog = make_supervisor()
    watchdog.detector = StallDetector(0, 0, failures=2)
    monkeypatch.setattr(supervision, "monotonic", lambda: 700)
    watchdog._received()
    assert watchdog.detector.last_progress == 700
    assert not watchdog.detector.observe(730, 100, False)


def test_ghost_reserved_records_do_not_hide_idle_prefork_consumer(monkeypatch):
    monkeypatch.setattr(supervision.state, "active_requests", set())
    monkeypatch.setattr(supervision.state, "reserved_requests", {"ghost"})
    assert not make_supervisor({})._busy()
    assert make_supervisor({"waiting_for_child_acceptance": object()})._busy()
    # For other pool implementations we cannot prove that reserved work is idle.
    assert make_supervisor()._busy()


def test_active_long_task_always_prevents_restart(monkeypatch):
    monkeypatch.setattr(supervision.state, "active_requests", {"long_running"})
    assert make_supervisor({})._busy()


def test_backlog_includes_every_priority_key_without_purging():
    watchdog = make_supervisor()
    watchdog.keys = ("timer", "timer\x06\x163", "timer\x06\x166", "timer\x06\x169")
    watchdog.client = MagicMock()
    pipe = watchdog.client.pipeline.return_value.__enter__.return_value
    pipe.execute.return_value = [20, 10, 3, 7]
    assert watchdog._backlog() == 40
    assert [call.args[0] for call in pipe.llen.call_args_list] == list(watchdog.keys)
    assert not pipe.delete.called


@pytest.mark.parametrize("busy,backlog,error", [(True, 5, False), (False, 0, False), (False, 5, True)])
def test_shutdown_fallback_rechecks_active_work_and_broker(monkeypatch, busy, backlog, error):
    watchdog = make_supervisor()
    watchdog.detector = StallDetector(0, 0)
    watchdog.shutdown_grace = 60
    watchdog.stopped = MagicMock()
    watchdog.stopped.wait.return_value = False
    monkeypatch.setattr(watchdog, "_busy", lambda: busy)
    monkeypatch.setattr(watchdog, "_backlog", MagicMock(side_effect=OSError() if error else None, return_value=backlog))
    kill = MagicMock()
    hard_exit = MagicMock()
    monkeypatch.setattr(supervision.os, "kill", kill)
    monkeypatch.setattr(supervision.os, "_exit", hard_exit)
    watchdog._recover(5)
    kill.assert_called_once_with(supervision.os.getpid(), supervision.signal.SIGTERM)
    hard_exit.assert_not_called()


def test_unresponsive_idle_main_process_exits_to_container_restart(monkeypatch):
    watchdog = make_supervisor()
    watchdog.detector = StallDetector(0, 0)
    watchdog.shutdown_grace = 60
    watchdog.stopped = MagicMock()
    watchdog.stopped.wait.return_value = False
    monkeypatch.setattr(watchdog, "_busy", lambda: False)
    monkeypatch.setattr(watchdog, "_backlog", lambda: 50)
    monkeypatch.setattr(supervision.os, "kill", MagicMock())
    hard_exit = MagicMock()
    monkeypatch.setattr(supervision.os, "_exit", hard_exit)
    watchdog._recover(50)
    hard_exit.assert_called_once_with(1)


def test_stop_during_graceful_shutdown_does_not_force_exit(monkeypatch):
    watchdog = make_supervisor()
    watchdog.detector = StallDetector(0, 0)
    watchdog.shutdown_grace = 60
    watchdog.stopped.set()
    monkeypatch.setattr(supervision.os, "kill", MagicMock())
    hard_exit = MagicMock()
    monkeypatch.setattr(supervision.os, "_exit", hard_exit)
    watchdog._recover(50)
    hard_exit.assert_not_called()


def test_mixed_or_non_timer_workers_are_not_supervised():
    consumer = SimpleNamespace(
        app=SimpleNamespace(conf=SimpleNamespace(get=lambda key, default=None: default)),
        connection=SimpleNamespace(transport=SimpleNamespace(driver_type="redis")),
        task_consumer=SimpleNamespace(channel=object(), queues=[SimpleNamespace(name="battle")]),
    )
    watchdog = RedisConsumerSupervisor(consumer)
    watchdog.start(consumer)
    assert watchdog.thread is None
