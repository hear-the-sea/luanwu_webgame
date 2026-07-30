from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay import signals as gameplay_signals
from gameplay.models import BotPopulationRecomputeDemand, BotProfile, BotRuntimeRoutingState, Manor
from gameplay.services.manor.prestige import add_prestige_silver_locked
from gameplay.services.raid.combat import battle as raid_battle
from gameplay.services.virtual_player_core import population_runtime
from gameplay.services.virtual_player_core.population_runtime import (
    merge_committed_prestige_transition_population_demands,
)

pytestmark = pytest.mark.django_db


def _profile_for_manor(
    manor: Manor,
    *,
    current_band: str = "newbie",
    engine_version: int = 1,
) -> BotProfile:
    now = timezone.now()
    fields = {
        "manor": manor,
        "archetype": BotProfile.Archetype.BALANCED,
        "state": BotProfile.State.ACTIVE,
        "prestige_band": "newbie",
        "target_prestige_band": "newbie",
        "current_prestige_band": current_band,
        "growth_seed": 81_001,
        "next_growth_at": now + timedelta(hours=1),
        "abandon_at": now + timedelta(days=30),
        "retire_at": now + timedelta(days=60),
        "engine_version": engine_version,
    }
    if engine_version == 2:
        fields.update(
            {
                "rng_version": 1,
                "plan_schema_version": 1,
                "policy_version": 1,
                "policy_checksum": "a" * 64,
                "last_strength_increase_at": now,
                "v2_enrolled_at": now,
            }
        )
    return BotProfile.objects.create(**fields)


def test_manor_prestige_fields_are_registered_readonly_in_admin() -> None:
    from django.contrib import admin

    from gameplay.admin import ManorAdmin

    assert Manor in admin.site._registry
    model_admin = admin.site._registry[Manor]
    assert isinstance(model_admin, ManorAdmin)
    assert {"prestige", "prestige_silver_spent"} <= set(model_admin.readonly_fields)


def test_real_prestige_transition_merges_only_old_and_new_cells_after_commit(
    django_user_model,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    user = django_user_model.objects.create_user(
        username="prestige_transition_real",
        password="pass123",
    )
    manor = user.manor
    manor.region = "north"
    manor.prestige = 499
    manor.prestige_silver_spent = 0
    manor.save(update_fields=["region", "prestige", "prestige_silver_spent"])
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gameplay_signals,
        "_queue_virtual_player_population_reconcile",
        lambda *, region, prestige_band: queued.append((region, prestige_band)) or True,
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with transaction.atomic():
            locked = Manor.objects.select_for_update().get(pk=manor.pk)
            assert add_prestige_silver_locked(locked, 1000) == 1

    assert len(callbacks) == 1
    assert not BotPopulationRecomputeDemand.objects.exists()
    assert queued == []

    callbacks[0]()

    rows = list(
        BotPopulationRecomputeDemand.objects.order_by("prestige_band").values_list(
            "region",
            "prestige_band",
            "requested_revision",
        )
    )
    assert rows == [
        ("north", "junior", 1),
        ("north", "newbie", 1),
    ]
    assert queued == [("north", "newbie"), ("north", "junior")]


def test_rolled_back_prestige_transition_emits_no_demand_or_task(
    django_user_model,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    user = django_user_model.objects.create_user(
        username="prestige_transition_rollback",
        password="pass123",
    )
    manor = user.manor
    manor.region = "north"
    manor.prestige = 499
    manor.save(update_fields=["region", "prestige"])
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gameplay_signals,
        "_queue_virtual_player_population_reconcile",
        lambda *, region, prestige_band: queued.append((region, prestige_band)) or True,
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                locked = Manor.objects.select_for_update().get(pk=manor.pk)
                add_prestige_silver_locked(locked, 1000)
                raise RuntimeError("rollback")

    assert callbacks == []
    assert queued == []
    assert not BotPopulationRecomputeDemand.objects.exists()
    manor.refresh_from_db()
    assert manor.prestige == 499


def test_raid_pvp_transition_uses_post_commit_population_transport(
    django_user_model,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    attacker_user = django_user_model.objects.create_user(
        username="prestige_transition_raid_attacker",
        password="pass123",
    )
    defender_user = django_user_model.objects.create_user(
        username="prestige_transition_raid_defender",
        password="pass123",
    )
    attacker = attacker_user.manor
    defender = defender_user.manor
    attacker.region = defender.region = "east"
    attacker.prestige = 450
    defender.prestige = 520
    attacker.save(update_fields=["region", "prestige"])
    defender.save(update_fields=["region", "prestige"])
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gameplay_signals,
        "_queue_virtual_player_population_reconcile",
        lambda *, region, prestige_band: queued.append((region, prestige_band)) or True,
    )
    run = SimpleNamespace(
        attacker_id=attacker.id,
        defender_id=defender.id,
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with transaction.atomic():
            raid_battle._apply_prestige_changes(run, True)

    assert len(callbacks) == 2
    assert not BotPopulationRecomputeDemand.objects.exists()

    for callback in callbacks:
        callback()

    attacker.refresh_from_db()
    defender.refresh_from_db()
    assert attacker.prestige == 500
    assert defender.prestige == 500
    assert run.attacker_prestige_change == 50
    assert run.defender_prestige_change == -20
    assert list(
        BotPopulationRecomputeDemand.objects.order_by("prestige_band").values_list(
            "region",
            "prestige_band",
            "requested_revision",
        )
    ) == [
        ("east", "junior", 1),
        ("east", "newbie", 1),
    ]
    assert queued == [("east", "newbie"), ("east", "junior")]


def test_continuous_real_transitions_coalesce_shared_band() -> None:
    now = timezone.now()
    user_model = Manor._meta.get_field("user").remote_field.model
    user = user_model.objects.create_user(
        username="prestige_transition_continuous",
        password="pass123",
    )
    manor = user.manor
    manor.region = "north"
    manor.save(update_fields=["region"])

    merge_committed_prestige_transition_population_demands(
        manor_id=manor.id,
        region="north",
        before_prestige=499,
        after_prestige=500,
        now=now,
    )
    merge_committed_prestige_transition_population_demands(
        manor_id=manor.id,
        region="north",
        before_prestige=1999,
        after_prestige=2000,
        now=now,
    )

    revisions = dict(
        BotPopulationRecomputeDemand.objects.values_list(
            "prestige_band",
            "requested_revision",
        )
    )
    assert revisions == {"newbie": 1, "junior": 2, "middle": 1}


def test_bot_transition_syncs_current_band_before_durable_handoff_and_is_idempotent(
    django_user_model,
) -> None:
    user = django_user_model.objects.create_user(
        username="prestige_transition_bot",
        password="pass123",
    )
    manor = user.manor
    manor.region = "east"
    manor.prestige = 500
    manor.save(update_fields=["region", "prestige"])
    profile = _profile_for_manor(manor)
    target_band = profile.target_prestige_band
    historical_band = profile.prestige_band
    now = timezone.now()

    first = merge_committed_prestige_transition_population_demands(
        manor_id=manor.id,
        region="east",
        before_prestige=499,
        after_prestige=500,
        now=now,
    )
    profile.refresh_from_db()
    first_updated_at = profile.updated_at

    assert [(row.region, row.prestige_band) for row in first] == [
        ("east", "newbie"),
        ("east", "junior"),
    ]
    assert profile.current_prestige_band == "junior"
    assert profile.target_prestige_band == target_band
    assert profile.prestige_band == historical_band

    second = merge_committed_prestige_transition_population_demands(
        manor_id=manor.id,
        region="east",
        before_prestige=499,
        after_prestige=500,
        now=now,
    )
    profile.refresh_from_db()

    assert profile.updated_at == first_updated_at
    assert [row.requested_revision for row in second] == [2, 2]
    assert profile.target_prestige_band == target_band
    assert profile.prestige_band == historical_band


def test_bot_transition_reuses_already_committed_current_band(
    django_user_model,
    monkeypatch,
) -> None:
    user = django_user_model.objects.create_user(
        username="prestige_transition_bot_already_synced",
        password="pass123",
    )
    manor = user.manor
    manor.region = "east"
    manor.prestige = 500
    manor.save(update_fields=["region", "prestige"])
    _profile_for_manor(manor, current_band="junior")

    def fail_sync(*_args, **_kwargs):
        raise AssertionError("already-synced transition must not reload the profile")

    monkeypatch.setattr(
        population_runtime.profile_store,
        "sync_current_prestige_band_from_manor",
        fail_sync,
    )

    demands = merge_committed_prestige_transition_population_demands(
        manor_id=manor.id,
        region="east",
        before_prestige=499,
        after_prestige=500,
        now=timezone.now(),
    )

    assert [(demand.region, demand.prestige_band) for demand in demands] == [
        ("east", "newbie"),
        ("east", "junior"),
    ]


def test_bot_profile_sync_rolls_back_when_population_handoff_fails(
    django_user_model,
    monkeypatch,
) -> None:
    user = django_user_model.objects.create_user(
        username="prestige_transition_bot_rollback",
        password="pass123",
    )
    manor = user.manor
    manor.region = "south"
    manor.prestige = 500
    manor.save(update_fields=["region", "prestige"])
    profile = _profile_for_manor(manor)

    def fail_merge(*args, **kwargs):
        raise RuntimeError("demand merge failed")

    monkeypatch.setattr(
        population_runtime,
        "_merge_normalized_population_recompute_demands_locked",
        fail_merge,
    )

    with pytest.raises(RuntimeError, match="demand merge failed"):
        merge_committed_prestige_transition_population_demands(
            manor_id=manor.id,
            region="south",
            before_prestige=499,
            after_prestige=500,
        )

    profile.refresh_from_db()
    assert profile.current_prestige_band == "newbie"
    assert not BotPopulationRecomputeDemand.objects.exists()


def test_periodic_roll_recovers_bot_band_when_post_commit_handoff_fails(
    django_user_model,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
    )
    user = django_user_model.objects.create_user(
        username="prestige_transition_periodic_recovery",
        password="pass123",
    )
    manor = user.manor
    manor.region = "south"
    manor.prestige = 499
    manor.prestige_silver_spent = 0
    manor.save(update_fields=["region", "prestige", "prestige_silver_spent"])
    profile = _profile_for_manor(manor, engine_version=2)
    monkeypatch.setattr(
        gameplay_signals,
        "merge_committed_prestige_transition_population_demands",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("injected prestige handoff failure")),
    )

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            locked = Manor.objects.select_for_update().get(pk=manor.pk)
            assert add_prestige_silver_locked(locked, 1000) == 1

    profile.refresh_from_db()
    assert profile.current_prestige_band == "newbie"
    assert not BotPopulationRecomputeDemand.objects.exists()

    assert population_runtime.roll_virtual_player_population(limit=0) == 0

    profile.refresh_from_db()
    assert profile.current_prestige_band == "junior"
    assert set(
        BotPopulationRecomputeDemand.objects.filter(region="south").values_list(
            "prestige_band",
            flat=True,
        )
    ) >= {"newbie", "junior"}


def test_staff_transition_does_not_create_virtual_population_demand(
    django_user_model,
) -> None:
    user = django_user_model.objects.create_user(
        username="prestige_transition_staff",
        password="pass123",
        is_staff=True,
    )
    manor = user.manor
    manor.region = "north"
    manor.save(update_fields=["region"])

    result = merge_committed_prestige_transition_population_demands(
        manor_id=manor.id,
        region="north",
        before_prestige=499,
        after_prestige=500,
    )

    assert result == ()
    assert not BotPopulationRecomputeDemand.objects.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "manor_id": 0,
            "region": "north",
            "before_prestige": 499,
            "after_prestige": 500,
        },
        {
            "manor_id": 1,
            "region": "invalid",
            "before_prestige": 100,
            "after_prestige": 101,
        },
    ],
)
def test_transition_handoff_rejects_invalid_identity_even_for_same_band(
    kwargs,
) -> None:
    with pytest.raises(population_runtime.PopulationRecomputeDemandError):
        merge_committed_prestige_transition_population_demands(**kwargs)

    assert not BotPopulationRecomputeDemand.objects.exists()
