from __future__ import annotations

import pytest
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from guests.models import GuestRecruitment, RecruitmentPool
from tests.guests.support import create_manor


@pytest.mark.django_db
def test_direct_orm_rejects_second_pending_guest_recruitment_for_same_manor(django_user_model):
    manor = create_manor(django_user_model, username="guest_recruit_unique", silver=5000)
    pool = RecruitmentPool.objects.create(key="unique-pending", name="唯一招募", cost={}, cooldown_seconds=0)
    complete_at = timezone.now()

    GuestRecruitment.objects.create(
        manor=manor,
        pool=pool,
        cost={},
        draw_count=1,
        duration_seconds=0,
        seed=1,
        status=GuestRecruitment.Status.PENDING,
        complete_at=complete_at,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            GuestRecruitment.objects.create(
                manor=manor,
                pool=pool,
                cost={},
                draw_count=1,
                duration_seconds=0,
                seed=2,
                status=GuestRecruitment.Status.PENDING,
                complete_at=complete_at,
            )
