from __future__ import annotations

import pytest
from django.utils import timezone

from core.config import GUEST
from core.exceptions import RecruitmentCandidateStateError, RetainerCapacityFullError
from guests.models import Guest, RecruitmentCandidate, RecruitmentPool
from guests.services.recruitment import recruit_guest, reveal_candidate_rarity
from guests.services.recruitment_guests import convert_candidate_to_retainer, finalize_candidate
from guests.services.training import finalize_guest_training
from tests.guests.support import create_manor

MAX_GUEST_LEVEL = int(GUEST.MAX_LEVEL)


def _recruit_candidate(manor, *, seed: int):
    return recruit_guest(manor, RecruitmentPool.objects.get(key="cunmu"), seed=seed)[0]


@pytest.mark.django_db
def test_finalize_guest_training_is_idempotent(game_data, django_user_model, load_guest_data):
    manor = create_manor(django_user_model, username="player_train2", silver=2000)
    guest = finalize_candidate(_recruit_candidate(manor, seed=1))

    guest.level = MAX_GUEST_LEVEL - 1
    guest.training_complete_at = timezone.now()
    guest.training_target_level = MAX_GUEST_LEVEL
    guest.save(update_fields=["level", "training_complete_at", "training_target_level"])

    guest.refresh_from_db()
    completed_at = guest.training_complete_at
    assert completed_at is not None

    first = finalize_guest_training(guest, now=completed_at)
    guest.refresh_from_db()
    level_after = guest.level

    second = finalize_guest_training(guest, now=timezone.now())
    guest.refresh_from_db()

    assert first is True
    assert second is False
    assert guest.level == MAX_GUEST_LEVEL
    assert guest.level == level_after


@pytest.mark.django_db
def test_reveal_candidate_rarity_marks_all(game_data, django_user_model, load_guest_data):
    manor = create_manor(django_user_model, username="player_magnify")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidates = recruit_guest(manor, pool, seed=2)
    assert any(not candidate.rarity_revealed for candidate in candidates)

    updated = reveal_candidate_rarity(manor)
    assert updated == len(candidates)
    for candidate in manor.candidates.all():
        assert candidate.rarity_revealed is True


@pytest.mark.django_db
def test_convert_candidate_to_retainer_rejects_missing_candidate(game_data, django_user_model, load_guest_data):
    manor = create_manor(
        django_user_model,
        username="player_retainer_missing_candidate",
        silver=500000,
        grain=500000,
    )

    candidate = _recruit_candidate(manor, seed=1)
    candidate_id = candidate.pk
    before_count = manor.retainer_count

    candidate.delete()

    with pytest.raises(RecruitmentCandidateStateError, match="候选门客不存在或已处理"):
        convert_candidate_to_retainer(candidate)

    manor.refresh_from_db()
    assert manor.retainer_count == before_count
    assert RecruitmentCandidate.objects.filter(pk=candidate_id).exists() is False


@pytest.mark.django_db
def test_finalize_candidate_rejects_missing_candidate(game_data, django_user_model, load_guest_data):
    manor = create_manor(
        django_user_model,
        username="player_finalize_missing_candidate",
        silver=500000,
        grain=500000,
    )

    candidate = _recruit_candidate(manor, seed=7)
    candidate_id = candidate.pk

    candidate.delete()

    with pytest.raises(RecruitmentCandidateStateError, match="候选门客不存在或已处理"):
        finalize_candidate(candidate)

    assert RecruitmentCandidate.objects.filter(pk=candidate_id).exists() is False
    assert Guest.objects.filter(manor=manor).count() == 0


@pytest.mark.django_db
def test_convert_candidate_to_retainer_rejects_when_capacity_full(game_data, django_user_model, load_guest_data):
    manor = create_manor(
        django_user_model,
        username="player_retainer_capacity_full",
        silver=500000,
        grain=500000,
    )
    manor.retainer_count = manor.retainer_capacity
    manor.save(update_fields=["retainer_count"])

    candidate = _recruit_candidate(manor, seed=1)

    with pytest.raises(RetainerCapacityFullError):
        convert_candidate_to_retainer(candidate)

    manor.refresh_from_db()
    assert manor.retainer_count == manor.retainer_capacity
    assert RecruitmentCandidate.objects.filter(pk=candidate.pk).exists()
