from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from gameplay.models import WorkAssignment, WorkTemplate
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate


def _create_work_data(manor, suffix: str) -> tuple[Guest, WorkTemplate]:
    guest_template = GuestTemplate.objects.create(
        key=f"view_work_boundary_guest_tpl_{suffix}_{manor.id}",
        name=f"打工边界门客模板{suffix}",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=guest_template,
        status=GuestStatus.IDLE,
    )
    work_template = WorkTemplate.objects.create(
        key=f"view_work_boundary_template_{suffix}_{manor.id}",
        name=f"打工边界模板{suffix}",
        tier=WorkTemplate.Tier.JUNIOR,
        required_level=1,
        required_force=0,
        required_intellect=0,
        reward_silver=100,
        work_duration=3600,
        display_order=0,
    )
    return guest, work_template


@pytest.mark.django_db
def test_assign_work_view_uses_refreshing_service_command(manor_with_user, monkeypatch):
    manor, client = manor_with_user
    guest, work_template = _create_work_data(manor, "assign_service_command")
    called: dict[str, object] = {}

    def _fake_assign(*, manor, guest, work_template):
        called["manor"] = manor
        called["guest"] = guest
        called["work_template"] = work_template
        return None

    monkeypatch.setattr("gameplay.views.work.assign_guest_to_work_with_refresh", _fake_assign)

    response = client.post(
        reverse("gameplay:assign_work"),
        {"guest_id": guest.id, "work_key": work_template.key},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:work")
    assert called == {
        "manor": manor,
        "guest": guest,
        "work_template": work_template,
    }


@pytest.mark.django_db
def test_recall_work_view_uses_refreshing_service_command(manor_with_user, monkeypatch):
    manor, client = manor_with_user
    guest, work_template = _create_work_data(manor, "recall_service_command")
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        status=WorkAssignment.Status.WORKING,
        complete_at=timezone.now() + timezone.timedelta(minutes=30),
    )
    called: dict[str, object] = {}

    def _fake_recall(*, manor, assignment):
        called["manor"] = manor
        called["assignment"] = assignment
        return True

    monkeypatch.setattr("gameplay.views.work.recall_guest_from_work_with_refresh", _fake_recall)

    response = client.post(reverse("gameplay:recall_work", kwargs={"pk": assignment.pk}))

    assert response.status_code == 302
    assert response.url == reverse("gameplay:work")
    assert called == {
        "manor": manor,
        "assignment": assignment,
    }


@pytest.mark.django_db
def test_claim_work_reward_view_uses_refreshing_service_command(manor_with_user, monkeypatch):
    manor, client = manor_with_user
    guest, work_template = _create_work_data(manor, "claim_service_command")
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        status=WorkAssignment.Status.COMPLETED,
        complete_at=timezone.now(),
    )
    called: dict[str, object] = {}

    def _fake_claim(*, manor, assignment):
        called["manor"] = manor
        called["assignment"] = assignment
        return {"silver": work_template.reward_silver}

    monkeypatch.setattr("gameplay.views.work.claim_work_reward_with_refresh", _fake_claim)

    response = client.post(reverse("gameplay:claim_work_reward", kwargs={"pk": assignment.pk}))

    assert response.status_code == 302
    assert response.url == reverse("gameplay:work")
    assert called == {
        "manor": manor,
        "assignment": assignment,
    }
