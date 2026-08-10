from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from accounts.forms import SignUpForm
from accounts.register_runtime import PUBLIC_REAL_USER_REGISTRATION_MARKER, prepare_signup_user
from gameplay import signals as gameplay_signals
from gameplay.constants import REGION_CHOICES
from gameplay.models import BotPopulationRecomputeDemand, BotRuntimeRoutingState
from gameplay.services.virtual_player_core import population_runtime
from gameplay.services.virtual_player_core.config import V2_PRESTIGE_BAND_NAMES
from gameplay.services.virtual_player_core.population_runtime import merge_real_player_population_recompute_demand

pytestmark = pytest.mark.django_db


def test_prepare_signup_user_sets_explicit_public_registration_marker() -> None:
    form = SignUpForm(
        data={
            "username": "population_signup_marker",
            "email": "population-signup-marker@example.com",
            "manor_name": "人口注册标记庄园",
            "region": "north",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        }
    )
    assert form.is_valid()

    user = prepare_signup_user(form=form)

    assert user.pk is None
    assert getattr(user, PUBLIC_REAL_USER_REGISTRATION_MARKER) is True
    assert user._signup_region == "north"
    assert user._signup_manor_name == "人口注册标记庄园"


def test_public_registration_merges_and_queues_population_after_commit(
    client,
    monkeypatch,
    django_capture_on_commit_callbacks,
) -> None:
    cache.clear()
    queued = []
    monkeypatch.setattr(
        gameplay_signals,
        "_queue_virtual_player_population_reconcile",
        lambda **kwargs: queued.append(kwargs) or True,
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = client.post(
            reverse("accounts:register"),
            {
                "username": "population_public_signup",
                "email": "population-public-signup@example.com",
                "manor_name": "人口公共注册庄园",
                "region": "north",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            REMOTE_ADDR="203.0.113.171",
        )

    assert response.status_code == 302
    assert not BotPopulationRecomputeDemand.objects.exists()
    assert queued == []
    assert len(callbacks) == 1

    callbacks[0]()

    demand = BotPopulationRecomputeDemand.objects.get()
    assert (demand.region, demand.prestige_band) == ("north", "newbie")
    assert (demand.requested_revision, demand.completed_revision) == (1, 0)
    assert queued == [{"region": "north", "prestige_band": "newbie"}]


def test_internal_admin_and_fixture_users_bootstrap_manor_without_population_event(
    monkeypatch,
    django_user_model,
    django_capture_on_commit_callbacks,
) -> None:
    callback = Mock()
    monkeypatch.setattr(
        gameplay_signals,
        "_merge_and_queue_registration_population",
        callback,
    )

    internal = django_user_model(username="population_internal_user", is_active=False)
    internal.set_unusable_password()
    internal._signup_region = "north"
    internal._virtual_player_internal = True
    setattr(internal, PUBLIC_REAL_USER_REGISTRATION_MARKER, True)

    staff = django_user_model(username="population_staff_user", is_staff=True)
    staff.set_unusable_password()
    staff._signup_region = "north"
    setattr(staff, PUBLIC_REAL_USER_REGISTRATION_MARKER, True)

    fixture_user = django_user_model(username="population_fixture_user")
    fixture_user.set_unusable_password()
    fixture_user._signup_region = "north"

    with django_capture_on_commit_callbacks(execute=True):
        internal.save()
        staff.save()
        fixture_user.save()

    callback.assert_not_called()
    assert internal.manor.region == "north"
    assert staff.manor.region == "north"
    assert fixture_user.manor.region == "north"
    assert not BotPopulationRecomputeDemand.objects.exists()


def test_overseas_registration_merges_virtual_population_cell() -> None:
    result = merge_real_player_population_recompute_demand(
        region="overseas",
        prestige=0,
        now=timezone.now(),
    )

    assert result is not None
    assert (result.region, result.prestige_band) == ("overseas", "newbie")
    assert BotPopulationRecomputeDemand.objects.filter(
        region="overseas",
        prestige_band="newbie",
    ).exists()


def test_periodic_roll_recovers_registration_when_post_commit_merge_fails(
    client,
    monkeypatch,
    django_capture_on_commit_callbacks,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
    )
    monkeypatch.setattr(
        gameplay_signals,
        "merge_real_player_population_recompute_demand",
        Mock(side_effect=RuntimeError("injected registration merge failure")),
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("accounts:register"),
            {
                "username": "population_recovered_signup",
                "email": "population-recovered-signup@example.com",
                "manor_name": "人口恢复注册庄园",
                "region": "north",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
            REMOTE_ADDR="203.0.113.172",
        )

    assert response.status_code == 302
    assert not BotPopulationRecomputeDemand.objects.exists()

    assert population_runtime.roll_virtual_player_population(limit=0) == 0

    expected_count = sum(1 for _region, _label in REGION_CHOICES for _prestige_band in V2_PRESTIGE_BAND_NAMES)
    assert BotPopulationRecomputeDemand.objects.count() == expected_count
    recovered = BotPopulationRecomputeDemand.objects.get(
        region="north",
        prestige_band="newbie",
    )
    assert (recovered.requested_revision, recovered.completed_revision) == (1, 0)
