from __future__ import annotations

from types import SimpleNamespace

import pytest

import gameplay.services.missions_impl.execution as mission_execution
from core.exceptions import MessageError
from gameplay.services.missions_impl import finalize_command as mission_finalize_command


def _mission_run(
    run_id: int, *, manor_id: int, user_id: int, mission_key: str = "mission_key", mission_name: str = "任务名"
):
    return SimpleNamespace(
        id=run_id,
        manor_id=manor_id,
        is_retreating=False,
        manor=SimpleNamespace(user_id=user_id),
        mission=SimpleNamespace(key=mission_key, name=mission_name),
    )


def _send_report(run, report):
    return mission_execution.send_mission_report_message(
        run,
        report,
        logger=mission_execution.logger,
        create_message=mission_execution.create_message,
        notify_user=mission_execution.notify_user,
        notification_infrastructure_exceptions=mission_execution.MISSION_NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS,
    )


def test_send_mission_report_message_ignores_explicit_message_failure(monkeypatch):
    run = _mission_run(88, manor_id=9, user_id=100)
    report = SimpleNamespace(id=66)

    monkeypatch.setattr(
        mission_execution,
        "create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )

    _send_report(run, report)


def test_send_mission_report_message_runtime_marker_error_bubbles_up(monkeypatch):
    run = _mission_run(188, manor_id=109, user_id=110)
    report = SimpleNamespace(id=166)

    monkeypatch.setattr(
        mission_execution,
        "create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        _send_report(run, report)


def test_send_mission_report_message_programming_error_bubbles_up(monkeypatch):
    run = _mission_run(89, manor_id=10, user_id=101)
    report = SimpleNamespace(id=67)

    monkeypatch.setattr(
        mission_execution,
        "create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken mission message contract")),
    )

    with pytest.raises(AssertionError, match="broken mission message contract"):
        _send_report(run, report)


def test_send_mission_report_notification_programming_error_bubbles_up(monkeypatch):
    run = _mission_run(90, manor_id=11, user_id=102)
    report = SimpleNamespace(id=68)

    monkeypatch.setattr(mission_execution, "create_message", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        mission_execution,
        "notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken mission notify contract")),
    )

    with pytest.raises(AssertionError, match="broken mission notify contract"):
        _send_report(run, report)


def test_send_mission_report_notification_runtime_marker_error_bubbles_up(monkeypatch):
    run = _mission_run(190, manor_id=111, user_id=112)
    report = SimpleNamespace(id=168)

    monkeypatch.setattr(mission_execution, "create_message", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        mission_execution,
        "notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ws backend down")),
    )

    with pytest.raises(RuntimeError, match="ws backend down"):
        _send_report(run, report)


@pytest.mark.django_db
def test_finalize_mission_run_defers_report_message_until_after_commit(monkeypatch):
    callbacks: list[object] = []
    reported: list[tuple[int, int]] = []
    report = SimpleNamespace(id=808)
    locked_run = SimpleNamespace(
        pk=707,
        id=707,
        status="active",
        Status=SimpleNamespace(COMPLETED="completed"),
        mission=SimpleNamespace(is_defense=False),
        is_retreating=False,
        manor=SimpleNamespace(user_id=33),
        manor_id=22,
    )

    monkeypatch.setattr(mission_finalize_command.transaction, "on_commit", lambda callback: callbacks.append(callback))

    mission_finalize_command.finalize_mission_run(
        SimpleNamespace(pk=707),
        now=SimpleNamespace(),
        load_locked_mission_run=lambda _pk: locked_run,
        build_defense_report_if_needed=lambda _run: report,
        extract_report_guest_state=lambda *_args, **_kwargs: ({}, set(), []),
        select_guests_for_finalize=lambda *_args, **_kwargs: [],
        prepare_guest_updates_for_finalize=lambda *_args, **_kwargs: ([], []),
        mark_run_completed=lambda current_run, _now: setattr(current_run, "status", current_run.Status.COMPLETED),
        apply_city_defense_battle_damage=lambda *_args, **_kwargs: None,
        apply_defender_troop_losses=lambda *_args, **_kwargs: None,
        return_attacker_troops_after_mission=lambda *_args, **_kwargs: None,
        apply_mission_rewards_if_won=lambda *_args, **_kwargs: None,
        send_mission_report_message=lambda current_run, current_report: reported.append(
            (current_run.id, current_report.id)
        ),
    )

    assert reported == []
    assert len(callbacks) == 1

    callbacks[0]()

    assert reported == [(707, 808)]
