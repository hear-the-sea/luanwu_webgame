from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone as django_timezone

from core.exceptions import JailError
from gameplay.models import InventoryItem, JailInteractionLog, JailPrisoner
from gameplay.services import jail as jail_service
from gameplay.services.jail import recruit_prisoner
from gameplay.services.jail_persuasion.interactions import interact_prisoner, observe_prisoner
from gameplay.services.jail_persuasion.profiles import METHOD_KINDNESS
from guests.models import Guest

pytestmark = pytest.mark.django_db


class FixedRng:
    def __init__(self, roll: int):
        self.roll = roll
        self.randint_calls: list[tuple[int, int]] = []

    def randint(self, start: int, end: int) -> int:
        self.randint_calls.append((start, end))
        if len(self.randint_calls) == 1:
            assert (start, end) == (1, 100)
            return self.roll
        if start <= 0 <= end:
            return 0
        return start

    def choice(self, values):
        return values[0]


def _set_prisoner(world, *, loyalty, affinity, stage):
    world.captor.guests.all().delete()
    prisoner = world.prisoner
    prisoner.loyalty = loyalty
    prisoner.affinity = affinity
    prisoner.milestone_stage = stage
    prisoner.save(update_fields=["loyalty", "affinity", "milestone_stage"])
    return prisoner


def _gold_quantity(world):
    return InventoryItem.objects.get(manor=world.captor, template=world.gold_template).quantity


@pytest.mark.parametrize(
    ("mode", "loyalty", "affinity", "stage", "expected_cost", "expected_loyalty"),
    [
        ("standard", 30, 0, 0, 1, 35),
        ("negotiated", 45, 60, 1, 1, 50),
        ("heartfelt", 80, 100, 2, 3, 65),
    ],
)
def test_successful_recruitment_returns_public_result_and_audit_log(
    persuasion_world,
    monkeypatch,
    mode,
    loyalty,
    affinity,
    stage,
    expected_cost,
    expected_loyalty,
):
    prisoner = _set_prisoner(persuasion_world, loyalty=loyalty, affinity=affinity, stage=stage)
    rng = FixedRng(1)
    granted_skills: list[Guest] = []
    auto_trained_guests: list[Guest] = []
    monkeypatch.setattr(jail_service, "grant_template_skills", granted_skills.append)
    monkeypatch.setattr(jail_service, "ensure_auto_training", auto_trained_guests.append)

    result = recruit_prisoner(persuasion_world.captor, prisoner.id, mode=mode, rng=rng)

    prisoner.refresh_from_db()
    log = JailInteractionLog.objects.get(prisoner=prisoner, attempt_scope="recruitment")
    assert isinstance(result, jail_service.RecruitmentResult)
    assert result.recruited is True
    assert result.mode == mode
    assert result.prisoner.pk == prisoner.pk
    assert result.guest is not None
    assert granted_skills == [result.guest]
    assert auto_trained_guests == [result.guest]
    assert result.guest.level == 1
    assert result.guest.loyalty == expected_loyalty
    assert result.gold_cost == expected_cost
    assert result.initial_loyalty == expected_loyalty
    assert result.copy_key.startswith(f"recruitment.{mode}.")
    assert result.copy_params == {
        "prisoner_name": prisoner.display_name,
        "new_loyalty": expected_loyalty,
    }
    assert result.copy_text
    assert not hasattr(result, "success_percent")
    assert not hasattr(result, "probability")
    assert not hasattr(result, "roll")
    with pytest.raises(FrozenInstanceError):
        result.mode = "other"
    assert prisoner.status == JailPrisoner.Status.RECRUITED
    assert _gold_quantity(persuasion_world) == 10 - expected_cost
    assert rng.randint_calls[0] == (1, 100)
    assert log.method == "recruitment"
    assert log.usage_date == django_timezone.localdate()
    assert log.resource_cost == {"gold_bar": expected_cost}
    assert log.outcome == JailInteractionLog.Outcome.RECRUITED
    assert log.heart_before == log.heart_after == loyalty
    assert log.affinity_before == log.affinity_after == affinity
    assert log.speaker is None
    assert log.speaker_name_snapshot == ""
    assert log.speaker_template_key_snapshot == ""
    assert log.copy_params == {
        **result.copy_params,
        "mode": mode,
        "success_percent": log.copy_params["success_percent"],
        "roll": 1,
    }


def test_failed_recruitment_consumes_gold_but_keeps_prisoner_state(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=7, stage=0)
    state_before = (prisoner.status, prisoner.loyalty, prisoner.affinity)

    result = recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(100))

    prisoner.refresh_from_db()
    log = JailInteractionLog.objects.get(prisoner=prisoner, attempt_scope="recruitment")
    assert result.recruited is False
    assert result.guest is None
    assert result.initial_loyalty is None
    assert result.gold_cost == 1
    assert result.copy_key.startswith("recruitment.failure.standard.")
    assert result.copy_params == {"prisoner_name": prisoner.display_name, "new_loyalty": 0}
    assert prisoner.status == state_before[0]
    assert prisoner.loyalty == state_before[1]
    assert prisoner.affinity == state_before[2]
    assert _gold_quantity(persuasion_world) == 9
    assert log.outcome == JailInteractionLog.Outcome.FAILED
    assert log.heart_before == log.heart_after == 30
    assert log.affinity_before == log.affinity_after == 7
    assert log.resource_cost == {"gold_bar": 1}


def test_recruitment_is_limited_once_per_day_across_modes(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=100, stage=2)
    recruit_prisoner(persuasion_world.captor, prisoner.id, mode="standard", rng=FixedRng(100))

    with pytest.raises(JailError, match="今日已尝试归附"):
        recruit_prisoner(persuasion_world.captor, prisoner.id, mode="heartfelt", rng=FixedRng(1))

    assert _gold_quantity(persuasion_world) == 9
    assert JailInteractionLog.objects.filter(prisoner=prisoner, attempt_scope="recruitment").count() == 1


def test_recruitment_can_be_attempted_again_on_next_local_date(persuasion_world, monkeypatch):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=0, stage=0)
    first_day = date(2026, 7, 23)
    monkeypatch.setattr(django_timezone, "localdate", lambda: first_day)
    recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(100))

    monkeypatch.setattr(django_timezone, "localdate", lambda: first_day + timedelta(days=1))
    result = recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(1))

    assert result.recruited is True
    assert JailInteractionLog.objects.filter(prisoner=prisoner, attempt_scope="recruitment").count() == 2


def test_failed_recruitment_does_not_block_normal_persuasion(persuasion_world, monkeypatch):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=0, stage=0)
    recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(100))
    observe_prisoner(persuasion_world.captor, prisoner.id)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(persuasion_world.captor, prisoner.id, method=METHOD_KINDNESS)

    assert result.log.attempt_scope is None
    assert JailInteractionLog.objects.filter(prisoner=prisoner).count() == 2


def test_public_copy_params_hide_probability_and_roll(persuasion_world, monkeypatch):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=0, stage=0)
    original_create = JailInteractionLog.objects.create
    audit_params_written: list[dict[str, object]] = []

    def _capture_log_create(**values):
        audit_params_written.append(values["copy_params"])
        return original_create(**values)

    monkeypatch.setattr(JailInteractionLog.objects, "create", _capture_log_create)

    result = recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(100))

    assert result.copy_params is not audit_params_written[0]
    result.copy_params["success_percent"] = 999
    assert audit_params_written[0]["success_percent"] == 25
    log = JailInteractionLog.objects.get(prisoner=prisoner, attempt_scope="recruitment")
    assert result.copy_params is not log.copy_params
    assert set(result.copy_params) == {"prisoner_name", "new_loyalty", "success_percent"}
    assert log.copy_params["mode"] == "standard"
    assert log.copy_params["success_percent"] == 25
    assert log.copy_params["roll"] == 100


@pytest.mark.parametrize(
    ("roll", "expected_seed_tail"),
    [
        (1, ("standard", "recruitment-copy")),
        (100, ("standard", "2026-07-23", "recruitment-failure-copy")),
    ],
)
def test_recruitment_copy_seed_uses_local_date_only_for_failure(
    persuasion_world,
    monkeypatch,
    roll,
    expected_seed_tail,
):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=0, stage=0)
    usage_date = date(2026, 7, 23)
    original_stable_seed = jail_service.stable_seed
    seed_calls: list[tuple[object, ...]] = []

    def _spy_stable_seed(*parts):
        seed_calls.append(parts)
        return original_stable_seed(*parts)

    monkeypatch.setattr(django_timezone, "localdate", lambda: usage_date)
    monkeypatch.setattr(jail_service, "stable_seed", _spy_stable_seed)

    recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(roll))

    assert seed_calls == [(prisoner.id, *expected_seed_tail)]


def test_log_integrity_error_rolls_back_gold_guest_and_prisoner(persuasion_world, monkeypatch):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=0, stage=0)

    def _raise_integrity_error(**_values):
        raise IntegrityError("simulated recruitment attempt conflict")

    monkeypatch.setattr(JailInteractionLog.objects, "create", _raise_integrity_error)

    with pytest.raises(IntegrityError, match="simulated recruitment attempt conflict"):
        recruit_prisoner(persuasion_world.captor, prisoner.id, rng=FixedRng(1))

    prisoner.refresh_from_db()
    assert _gold_quantity(persuasion_world) == 10
    assert Guest.objects.filter(manor=persuasion_world.captor).count() == 0
    assert prisoner.status == JailPrisoner.Status.HELD


def test_recruitment_rejects_unmet_mode_condition(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=50, affinity=59, stage=1)
    with pytest.raises(JailError, match="尚未满足权宜归附条件"):
        recruit_prisoner(persuasion_world.captor, prisoner.id, mode="negotiated", rng=FixedRng(1))


def test_recruitment_is_blocked_by_pending_milestone(persuasion_world):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    prisoner = _set_prisoner(persuasion_world, loyalty=45, affinity=60, stage=0)

    with pytest.raises(JailError, match="先处理当前归心事件"):
        recruit_prisoner(persuasion_world.captor, prisoner.id, mode="negotiated", rng=FixedRng(1))
