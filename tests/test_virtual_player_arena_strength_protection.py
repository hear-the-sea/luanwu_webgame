from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gameplay.models import (
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotExternalStrengthReconciliation,
    BotProfile,
    BotRuntimeRoutingState,
    Manor,
)
from gameplay.services.arena import virtual_reserve_fill, virtual_reserve_pool
from gameplay.services.arena.virtual_protection import (
    is_virtual_profile_arena_match_eligible,
    with_arena_reconciliation_state,
)
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation

pytestmark = pytest.mark.django_db

FIXED_NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def arena_cap_policy(db):
    policy = release_configured_policy_operation(version=2, apply=True)
    BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        calibration_routes=[],
    )
    return policy


def _create_v2_profile(
    django_user_model,
    policy,
    *,
    username: str,
    prestige: int,
    state: str = BotProfile.State.ACTIVE,
) -> BotProfile:
    django_user_model.objects.bulk_create([django_user_model(username=username)])
    user = django_user_model.objects.get(username=username)
    manor = Manor.objects.create(
        user=user,
        region="north",
        prestige=prestige,
        last_active_at=FIXED_NOW,
    )
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=state,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=970_000 + int(user.pk),
        next_growth_at=FIXED_NOW + timedelta(hours=1),
        abandon_at=FIXED_NOW + timedelta(days=30),
        retire_at=FIXED_NOW + timedelta(days=60),
        engine_version=2,
        rng_version=1,
        plan_schema_version=1,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        development_profile={},
        last_strength_increase_at=FIXED_NOW - timedelta(days=1),
        v2_enrolled_at=FIXED_NOW - timedelta(days=1),
    )


def _create_reconciliation(
    profile: BotProfile,
    *,
    status: str,
) -> BotExternalStrengthReconciliation:
    values = {
        "profile_id": profile.id,
        "domain_event_kind": "arena_strength_test",
        "domain_event_id": f"profile-{profile.id}-{status}",
        "origin_committed_at": FIXED_NOW - timedelta(minutes=5),
        "pre_strength_summary": {
            "composite": 0,
            "components": {
                "arena_lineup_power": 0,
                "core_building_level": 0,
                "guest_count": 0,
                "max_guest_level": 0,
                "prestige": 0,
                "troop_total": 0,
            },
        },
        "pre_prestige_band": "newbie",
        "status": status,
        "available_at": FIXED_NOW,
    }
    if status in {
        BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE,
        BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
    }:
        values.update(
            claim_token=uuid4(),
            claimed_at=FIXED_NOW - timedelta(minutes=1),
            claim_expires_at=FIXED_NOW + timedelta(minutes=4),
        )
    if status in {
        BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
        BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
        BotExternalStrengthReconciliation.Status.APPLIED,
    }:
        values["profile_completed_at"] = FIXED_NOW - timedelta(minutes=2)
    if status == BotExternalStrengthReconciliation.Status.APPLIED:
        values["applied_at"] = FIXED_NOW
    if status == BotExternalStrengthReconciliation.Status.QUARANTINED:
        values.update(
            quarantined_at=FIXED_NOW,
            quarantined_phase=BotExternalStrengthReconciliation.Phase.PROFILE,
            failure_code="test_failure",
        )
    return BotExternalStrengthReconciliation.objects.create(**values)


def _create_demand() -> ArenaVirtualDemand:
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
    )
    return ArenaVirtualDemand.objects.create(
        tournament=tournament,
        target_guest_count=1,
        target_team_power=100,
        missing_entry_count=1,
        reserve_target_count=1,
        warm_target_count=1,
        max_reserve_target_count=1,
    )


@pytest.mark.parametrize(
    "status",
    [
        BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
        BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE,
        BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
        BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION,
        BotExternalStrengthReconciliation.Status.QUARANTINED,
    ],
)
def test_unresolved_reconciliation_is_excluded_by_query_and_runtime_protection(
    django_user_model,
    arena_cap_policy,
    status: str,
) -> None:
    profile = _create_v2_profile(
        django_user_model,
        arena_cap_policy,
        username=f"arena_unresolved_{status}",
        prestige=100,
    )
    _create_reconciliation(profile, status=status)

    protected_ids = list(
        with_arena_reconciliation_state(BotProfile.objects.filter(pk=profile.pk)).values_list("id", flat=True)
    )

    assert protected_ids == []
    assert is_virtual_profile_arena_match_eligible(profile, now=FIXED_NOW) is False


def test_applied_reconciliation_uses_live_cap_and_protection_is_read_only(
    django_user_model,
    arena_cap_policy,
    monkeypatch,
) -> None:
    within_cap = _create_v2_profile(
        django_user_model,
        arena_cap_policy,
        username="arena_applied_within_cap",
        prestige=100,
    )
    over_cap = _create_v2_profile(
        django_user_model,
        arena_cap_policy,
        username="arena_applied_over_cap",
        prestige=499,
    )
    _create_reconciliation(
        within_cap,
        status=BotExternalStrengthReconciliation.Status.APPLIED,
    )
    _create_reconciliation(
        over_cap,
        status=BotExternalStrengthReconciliation.Status.APPLIED,
    )

    with CaptureQueriesContext(connection) as captured:
        assert is_virtual_profile_arena_match_eligible(within_cap, now=FIXED_NOW) is True
        assert is_virtual_profile_arena_match_eligible(over_cap, now=FIXED_NOW) is False

    write_prefixes = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    assert [
        query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(write_prefixes)
    ] == []

    monkeypatch.setattr(
        virtual_reserve_fill,
        "_select_bot_lineup",
        lambda *_args, **_kwargs: [{"guest_id": 1}],
    )
    assert virtual_reserve_fill._eligible_bot_profile_ids(
        excluded_manor_ids=set(),
        mode="tournament",
        event_id=41,
        target_guest_count=1,
        target_team_power=100,
        candidate_profile_ids=[within_cap.id, over_cap.id],
        now=FIXED_NOW,
    ) == [within_cap.id]


def test_over_cap_recovery_is_rolled_back_before_candidate_lease(
    django_user_model,
    arena_cap_policy,
    monkeypatch,
) -> None:
    demand = _create_demand()
    profile = _create_v2_profile(
        django_user_model,
        arena_cap_policy,
        username="arena_over_cap_recovery",
        prestige=499,
        state=BotProfile.State.ABANDONED,
    )
    _create_reconciliation(
        profile,
        status=BotExternalStrengthReconciliation.Status.APPLIED,
    )

    def reactivate(profile_id: int, *, now):
        assert now == FIXED_NOW
        BotProfile.objects.filter(pk=profile_id).update(state=BotProfile.State.ACTIVE)
        return BotProfile.objects.select_related("manor").get(pk=profile_id)

    monkeypatch.setattr(
        virtual_reserve_pool,
        "reactivate_virtual_player_profile",
        reactivate,
    )

    leased = virtual_reserve_pool._lease_candidate(
        demand=demand,
        profile_id=profile.id,
        allowed_states=(BotProfile.State.ABANDONED,),
        member_state=ArenaVirtualReserveMember.State.READY,
        now=FIXED_NOW,
        recover=True,
    )

    profile.refresh_from_db()
    assert leased is None
    assert profile.state == BotProfile.State.ABANDONED
    assert not ArenaVirtualReserveMember.objects.filter(profile=profile).exists()


def test_reevaluation_releases_applied_member_that_is_now_over_cap(
    django_user_model,
    arena_cap_policy,
) -> None:
    demand = _create_demand()
    profile = _create_v2_profile(
        django_user_model,
        arena_cap_policy,
        username="arena_over_cap_existing_member",
        prestige=499,
    )
    _create_reconciliation(
        profile,
        status=BotExternalStrengthReconciliation.Status.APPLIED,
    )
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
        current_lineup_power=100,
    )

    virtual_reserve_pool.reevaluate_existing_members(demand, now=FIXED_NOW)

    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()
    assert BotProfile.objects.filter(pk=profile.pk).exists()


def test_locked_fill_recheck_rejects_profile_that_crossed_cap_after_scan(
    django_user_model,
    arena_cap_policy,
    monkeypatch,
) -> None:
    profile = _create_v2_profile(
        django_user_model,
        arena_cap_policy,
        username="arena_crosses_cap_after_scan",
        prestige=100,
    )
    _create_reconciliation(
        profile,
        status=BotExternalStrengthReconciliation.Status.APPLIED,
    )
    lineup_calls: list[int] = []

    def select_lineup(candidate: BotProfile, **_kwargs):
        lineup_calls.append(candidate.id)
        return [{"guest_id": 1}]

    monkeypatch.setattr(
        virtual_reserve_fill,
        "_select_bot_lineup",
        select_lineup,
    )
    eligible_ids = virtual_reserve_fill._eligible_bot_profile_ids(
        excluded_manor_ids=set(),
        mode="tournament",
        event_id=42,
        target_guest_count=1,
        target_team_power=100,
        candidate_profile_ids=[profile.id],
        now=FIXED_NOW,
    )
    assert eligible_ids == [profile.id]
    assert lineup_calls == [profile.id]

    Manor.objects.filter(pk=profile.manor_id).update(prestige=499)
    locked = virtual_reserve_fill._lock_eligible_bot_lineups(
        profile_ids=eligible_ids,
        excluded_manor_ids=set(),
        needed=1,
        mode="tournament",
        event_id=42,
        target_guest_count=1,
        target_team_power=100,
        now=FIXED_NOW,
    )

    assert locked == []
    assert lineup_calls == [profile.id]
