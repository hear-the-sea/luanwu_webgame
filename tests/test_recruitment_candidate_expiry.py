from __future__ import annotations

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from guests.models import GuestTemplate, RecruitmentCandidate, RecruitmentPool
from guests.tasks import cleanup_expired_recruitment_candidates
from tests.guests.support import create_manor


def _create_candidate(manor, pool, template, *, name: str) -> RecruitmentCandidate:
    return RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name=name,
        rarity=template.rarity,
        archetype=template.archetype,
    )


@pytest.mark.django_db
def test_expired_recruitment_candidates_are_deleted_and_cache_is_invalidated(game_data, django_user_model, monkeypatch):
    manor = create_manor(django_user_model, username="candidate_expiry_player")
    pool = RecruitmentPool.objects.get(key="cunmu")
    template = GuestTemplate.objects.filter(recruitable=True).first()
    assert template is not None

    now = timezone.now().replace(microsecond=0)
    expired = _create_candidate(manor, pool, template, name="过期候选")
    boundary = _create_candidate(manor, pool, template, name="边界候选")
    fresh = _create_candidate(manor, pool, template, name="新候选")
    RecruitmentCandidate.objects.filter(pk=expired.pk).update(created_at=now - timedelta(hours=24, seconds=1))
    RecruitmentCandidate.objects.filter(pk=boundary.pk).update(created_at=now - timedelta(hours=24))
    RecruitmentCandidate.objects.filter(pk=fresh.pk).update(created_at=now - timedelta(hours=23, minutes=59))

    invalidated_manors: list[int] = []
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guests.services.recruitment_shared.invalidate_recruitment_hall_cache",
        invalidated_manors.append,
    )

    assert cleanup_expired_recruitment_candidates.run() == 2
    assert not RecruitmentCandidate.objects.filter(pk__in=[expired.pk, boundary.pk]).exists()
    assert RecruitmentCandidate.objects.filter(pk=fresh.pk).exists()
    assert invalidated_manors == [manor.id]


@pytest.mark.django_db
def test_expired_recruitment_candidate_cleanup_is_bounded_and_scheduled(game_data, django_user_model, monkeypatch):
    manor = create_manor(django_user_model, username="candidate_expiry_batch")
    pool = RecruitmentPool.objects.get(key="cunmu")
    template = GuestTemplate.objects.filter(recruitable=True).first()
    assert template is not None

    now = timezone.now().replace(microsecond=0)
    candidates = [_create_candidate(manor, pool, template, name=f"过期候选{i}") for i in range(3)]
    RecruitmentCandidate.objects.filter(pk__in=[candidate.pk for candidate in candidates]).update(
        created_at=now - timedelta(hours=24, seconds=1)
    )
    monkeypatch.setattr("guests.tasks.timezone.now", lambda: now)
    monkeypatch.setattr("guests.services.recruitment_shared.invalidate_recruitment_hall_cache", lambda _manor_id: None)

    assert cleanup_expired_recruitment_candidates.run(limit=2) == 2
    assert RecruitmentCandidate.objects.filter(pk__in=[candidate.pk for candidate in candidates]).count() == 1

    schedule = settings.CELERY_BEAT_SCHEDULE["cleanup-expired-recruitment-candidates"]
    assert schedule["task"] == "guests.cleanup_expired_recruitment_candidates"
    assert schedule["schedule"].minute == set(range(0, 60, 5))
