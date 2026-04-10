from __future__ import annotations

import pytest

from battle.models import TroopTemplate
from core.exceptions import MessageError
from core.exceptions.recruitment_extended import (
    TroopRecruitmentNotFoundError,
    TroopRecruitmentNotReadyError,
    TroopTemplateNotFoundError,
)
from gameplay.models import PlayerTroop, TroopRecruitment
from gameplay.services.recruitment.recruitment import finalize_troop_recruitment
from tests.troop_recruitment_service.support import build_due_recruitment

pytest_plugins = ("tests.troop_recruitment_service.fixtures",)


def test_troop_recruitment_not_found_error_uses_specific_error_code():
    error = TroopRecruitmentNotFoundError()

    assert error.error_code == "troop_recruitment_not_found"
    assert error.error_code != MessageError.error_code


def test_troop_recruitment_not_ready_error_uses_specific_error_code():
    error = TroopRecruitmentNotReadyError()

    assert error.error_code == "troop_recruitment_not_ready"
    assert error.error_code != MessageError.error_code


def test_troop_template_not_found_error_uses_specific_error_code():
    error = TroopTemplateNotFoundError("scout")

    assert error.error_code == "troop_template_not_found"
    assert error.error_code != MessageError.error_code


@pytest.mark.django_db
def test_finalize_troop_recruitment_auto_creates_missing_troop_template(recruit_manor):
    manor = recruit_manor
    recruitment = build_due_recruitment(manor, troop_key="scout", troop_name="探子", quantity=3)

    finalize_troop_recruitment(recruitment, send_notification=False)

    recruitment.refresh_from_db()
    assert recruitment.status == TroopRecruitment.Status.COMPLETED

    template = TroopTemplate.objects.get(key="scout")
    troop = PlayerTroop.objects.get(manor=manor, troop_template=template)
    assert troop.count == 3


@pytest.mark.django_db
def test_finalize_troop_recruitment_keeps_success_when_explicit_failures(monkeypatch, recruit_manor):
    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)

    monkeypatch.setattr(
        "gameplay.services.utils.messages.create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )
    monkeypatch.setattr(
        "gameplay.services.utils.notifications.notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("ws backend down")),
    )

    finalize_troop_recruitment(recruitment, send_notification=True)
    recruitment.refresh_from_db()
    assert recruitment.status == TroopRecruitment.Status.COMPLETED


@pytest.mark.django_db
def test_finalize_troop_recruitment_message_runtime_marker_error_bubbles_up(monkeypatch, recruit_manor):
    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)

    monkeypatch.setattr(
        "gameplay.services.utils.messages.create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        finalize_troop_recruitment(recruitment, send_notification=True)

    recruitment.refresh_from_db()
    assert recruitment.status == TroopRecruitment.Status.COMPLETED


@pytest.mark.django_db
def test_finalize_troop_recruitment_notification_runtime_marker_error_bubbles_up(monkeypatch, recruit_manor):
    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)

    monkeypatch.setattr("gameplay.services.utils.messages.create_message", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "gameplay.services.utils.notifications.notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ws backend down")),
    )

    with pytest.raises(RuntimeError, match="ws backend down"):
        finalize_troop_recruitment(recruitment, send_notification=True)

    recruitment.refresh_from_db()
    assert recruitment.status == TroopRecruitment.Status.COMPLETED


@pytest.mark.django_db
def test_finalize_troop_recruitment_notification_programming_error_bubbles_up(monkeypatch, recruit_manor):
    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)

    monkeypatch.setattr("gameplay.services.utils.messages.create_message", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "gameplay.services.utils.notifications.notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken troop notify contract")),
    )

    with pytest.raises(AssertionError, match="broken troop notify contract"):
        finalize_troop_recruitment(recruitment, send_notification=True)

    recruitment.refresh_from_db()
    assert recruitment.status == TroopRecruitment.Status.COMPLETED


@pytest.mark.django_db
def test_finalize_troop_recruitment_raises_when_recruitment_not_found(recruit_manor):
    """Test that finalize raises TroopRecruitmentNotFoundError when recruitment doesn't exist."""
    from gameplay.models import TroopRecruitment

    # Create a recruitment and then delete it to simulate a non-existent record
    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)
    recruitment_id = recruitment.id
    recruitment.delete()

    # Create a fake recruitment object with the deleted ID
    fake_recruitment = TroopRecruitment(id=recruitment_id, manor_id=recruit_manor.id)

    with pytest.raises(TroopRecruitmentNotFoundError):
        finalize_troop_recruitment(fake_recruitment, send_notification=False)


@pytest.mark.django_db
def test_finalize_troop_recruitment_raises_when_status_not_recruiting(recruit_manor):
    """Test that finalize raises TroopRecruitmentNotReadyError when status is not RECRUITING."""
    from gameplay.models import TroopRecruitment

    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)
    recruitment.status = TroopRecruitment.Status.COMPLETED
    recruitment.save()

    with pytest.raises(TroopRecruitmentNotReadyError, match="募兵状态不正确"):
        finalize_troop_recruitment(recruitment, send_notification=False)


@pytest.mark.django_db
def test_finalize_troop_recruitment_raises_when_not_complete_yet(recruit_manor):
    """Test that finalize raises TroopRecruitmentNotReadyError when complete_at is in the future."""
    from datetime import timedelta

    from django.utils import timezone

    recruitment = build_due_recruitment(recruit_manor, troop_key="scout", troop_name="探子", quantity=2)
    # Set complete_at to 1 hour in the future
    recruitment.complete_at = timezone.now() + timedelta(hours=1)
    recruitment.save()

    with pytest.raises(TroopRecruitmentNotReadyError):
        finalize_troop_recruitment(recruitment, send_notification=False)


@pytest.mark.django_db
def test_finalize_troop_recruitment_raises_when_troop_template_not_found(monkeypatch, recruit_manor):
    """Test that finalize raises TroopTemplateNotFoundError when troop template can't be created."""
    recruitment = build_due_recruitment(recruit_manor, troop_key="nonexistent_troop", troop_name="不存在", quantity=2)

    # Mock get_troop_template to return None (template config not found)
    # The import is within _get_or_create_battle_troop_template
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.get_troop_template",
        lambda _key: None,
    )

    with pytest.raises(TroopTemplateNotFoundError, match="nonexistent_troop"):
        finalize_troop_recruitment(recruitment, send_notification=False)


@pytest.mark.parametrize(
    ("error_cls", "kwargs", "expected_error_code"),
    [
        (TroopRecruitmentNotFoundError, {"recruitment_id": 1}, "troop_recruitment_not_found"),
        (TroopRecruitmentNotReadyError, {"complete_at": "2026-04-09T00:00:00"}, "troop_recruitment_not_ready"),
        (TroopTemplateNotFoundError, {"troop_key": "scout"}, "troop_template_not_found"),
    ],
)
def test_recruitment_extended_exceptions_expose_specific_error_code(error_cls, kwargs, expected_error_code):
    error = error_cls(**kwargs)

    assert error.error_code == expected_error_code
    assert error.error_code != "game_error"
