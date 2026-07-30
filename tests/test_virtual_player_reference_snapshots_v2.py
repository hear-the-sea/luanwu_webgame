from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.db import transaction

from gameplay.models import BotProfile, Building, BuildingType, Manor
from gameplay.services.virtual_player_core import bootstrap
from gameplay.services.virtual_player_core.projection import ReferenceSource, SampleTier
from gameplay.services.virtual_player_core.reference_snapshots import (
    CORE_BUILDING_KEYS,
    build_strength_summary,
    load_human_reference_cohort,
)
from guests.models import Guest, GuestTemplate

FIXED_NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def released_v2_policy(db):
    from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation

    return release_configured_policy_operation(version=1, apply=True)


def _materialize_v2_plan(plan: bootstrap.V2BootstrapPlan) -> BotProfile:
    with transaction.atomic():
        population_permit = bootstrap._issue_v2_bootstrap_population_permit(
            region=plan.region,
            prestige_band=plan.prestige_band,
        )
        return bootstrap.create_virtual_player_v2(
            plan=plan,
            population_permit=population_permit,
            now=FIXED_NOW,
        )


def _create_human_references(
    *,
    user_model,
    count: int,
    region: str,
    prestige: int,
    prefix: str,
) -> list[Manor]:
    if count <= 0:
        return []
    users = [user_model(username=f"{prefix}_{index}") for index in range(count)]
    user_model.objects.bulk_create(users)
    manors = [
        Manor(
            user=user,
            name=f"{prefix}_manor_{index}",
            region=region,
            prestige=prestige + index,
            last_active_at=FIXED_NOW,
        )
        for index, user in enumerate(users)
    ]
    Manor.objects.bulk_create(manors)

    building_type = BuildingType.objects.filter(key__in=CORE_BUILDING_KEYS).first()
    guest_template = GuestTemplate.objects.order_by("id").first()
    assert building_type is not None
    assert guest_template is not None
    Building.objects.bulk_create(
        [
            Building(
                manor=manor,
                building_type=building_type,
                level=2 + index % 3,
            )
            for index, manor in enumerate(manors)
        ]
    )
    Guest.objects.bulk_create(
        [
            Guest(
                manor=manor,
                template=guest_template,
                level=8 + index % 5,
                force=80 + index,
                intellect=80 + index,
                defense_stat=80 + index,
            )
            for index, manor in enumerate(manors)
        ]
    )
    return manors


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("sample_count", "expected_tier"),
    [
        (0, SampleTier.NO_REFERENCE),
        (1, SampleTier.SPARSE),
        (4, SampleTier.SPARSE),
        (5, SampleTier.LIMITED),
        (29, SampleTier.LIMITED),
        (30, SampleTier.SUFFICIENT),
    ],
)
def test_v2_bootstrap_uses_real_local_sample_tier_boundaries(
    released_v2_policy,
    game_data,
    django_user_model,
    sample_count: int,
    expected_tier: SampleTier,
) -> None:
    _create_human_references(
        user_model=django_user_model,
        count=sample_count,
        region="north",
        prestige=2_500,
        prefix=f"tier_{sample_count}",
    )

    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "middle",
        BotProfile.Archetype.BALANCED,
        771_000 + sample_count,
        FIXED_NOW,
    )

    selection = plan.blueprint.reference_selection
    assert selection.local_sample_count == sample_count
    assert selection.tier is expected_tier
    assert selection.source is (ReferenceSource.STARTER if sample_count == 0 else ReferenceSource.LOCAL)
    assert tuple(selection.cap.components) == (
        "arena_lineup_power",
        "core_building_level",
        "guest_count",
        "max_guest_level",
        "prestige",
        "troop_total",
    )


@pytest.mark.django_db
def test_v2_reference_cohort_is_anonymous_bounded_and_uses_four_bulk_queries(
    game_data,
    django_user_model,
    django_assert_num_queries,
) -> None:
    manors = _create_human_references(
        user_model=django_user_model,
        count=3,
        region="north",
        prestige=2_600,
        prefix="bulk_reference",
    )

    with django_assert_num_queries(4):
        cohort = load_human_reference_cohort(
            region="north",
            prestige_band="middle",
            low=2_000,
            high=8_000,
            now=FIXED_NOW,
            candidate_limit=2,
        )

    assert cohort.local_sample_count == 3
    assert len(cohort.local_snapshots) == 2
    assert not cohort.global_same_band_snapshots
    for snapshot in cohort.local_snapshots:
        assert snapshot.business_key.startswith("human-ref-v1:")
        assert all(str(manor.id) != snapshot.business_key for manor in manors)
        assert snapshot.strength.composite == (snapshot.arena_lineup_power + 2 * snapshot.troop_total)


@pytest.mark.django_db
def test_v2_bootstrap_never_replaces_a_sparse_local_cohort_with_global_samples(
    released_v2_policy,
    game_data,
    django_user_model,
) -> None:
    _create_human_references(
        user_model=django_user_model,
        count=1,
        region="north",
        prestige=2_500,
        prefix="local_reference",
    )
    _create_human_references(
        user_model=django_user_model,
        count=30,
        region="south",
        prestige=3_000,
        prefix="global_reference",
    )

    local_plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "middle",
        BotProfile.Archetype.BALANCED,
        771_101,
        FIXED_NOW,
    )
    borrowed_plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "west",
        "middle",
        BotProfile.Archetype.BALANCED,
        771_102,
        FIXED_NOW,
    )

    local_selection = local_plan.blueprint.reference_selection
    assert local_selection.source is ReferenceSource.LOCAL
    assert local_selection.local_sample_count == 1
    assert local_selection.tier is SampleTier.SPARSE
    borrowed_selection = borrowed_plan.blueprint.reference_selection
    assert borrowed_selection.source is ReferenceSource.GLOBAL_SAME_BAND
    assert borrowed_selection.local_sample_count == 0
    assert borrowed_selection.tier is SampleTier.NO_REFERENCE


@pytest.mark.django_db
def test_v2_bootstrap_accepts_a_discounted_level_one_global_core_building(
    released_v2_policy,
    game_data,
    django_user_model,
) -> None:
    manors = _create_human_references(
        user_model=django_user_model,
        count=1,
        region="north",
        prestige=100,
        prefix="level_one_global_reference",
    )
    Building.objects.filter(manor=manors[0]).update(level=1)

    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "west",
        "newbie",
        BotProfile.Archetype.BALANCED,
        771_103,
        FIXED_NOW,
    )

    selection = plan.blueprint.reference_selection
    assert selection.source is ReferenceSource.GLOBAL_SAME_BAND
    assert selection.cap.components["core_building_level"] == 1
    assert plan.projection.building_level == 1


@pytest.mark.django_db
def test_v2_materialization_rejects_a_reference_cap_that_tightened_after_planning(
    released_v2_policy,
    game_data,
    django_user_model,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "middle",
        BotProfile.Archetype.BALANCED,
        771_201,
        FIXED_NOW,
    )
    _create_human_references(
        user_model=django_user_model,
        count=1,
        region="north",
        prestige=2_100,
        prefix="tightened_reference",
    )
    counts_before = (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )

    with pytest.raises(
        bootstrap.V2BootstrapError,
        match="reference strength cap tightened",
    ):
        _materialize_v2_plan(plan)

    assert (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before


@pytest.mark.django_db
def test_v2_materialization_rolls_back_when_actual_strength_exceeds_cap(
    released_v2_policy,
    game_data,
    django_user_model,
    monkeypatch,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "east",
        "middle",
        BotProfile.Archetype.GUARD,
        771_202,
        FIXED_NOW,
    )
    counts_before = (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )
    monkeypatch.setattr(
        bootstrap,
        "load_manor_strength_summary",
        lambda **_kwargs: build_strength_summary(
            prestige=plan.projection.prestige,
            core_building_level=plan.projection.building_level,
            guest_count=plan.projection.guest_count,
            max_guest_level=plan.projection.guest_level,
            arena_lineup_power=10_000_000,
            troop_total=plan.projection.troop_count,
        ),
    )

    with pytest.raises(
        bootstrap.V2BootstrapError,
        match="materialized V2 profile exceeds",
    ):
        _materialize_v2_plan(plan)

    assert (
        django_user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before


@pytest.mark.django_db
def test_v2_reference_window_excludes_stale_and_staff_profiles(
    game_data,
    django_user_model,
) -> None:
    valid, stale, staff, virtual = _create_human_references(
        user_model=django_user_model,
        count=4,
        region="north",
        prestige=2_700,
        prefix="eligibility_reference",
    )
    Manor.objects.filter(pk=stale.pk).update(last_active_at=FIXED_NOW - timedelta(days=31))
    django_user_model.objects.filter(pk=staff.user_id).update(is_staff=True)
    BotProfile.objects.create(
        manor=virtual,
        prestige_band="middle",
        target_prestige_band="middle",
        current_prestige_band="middle",
        growth_seed=991,
        next_growth_at=FIXED_NOW,
        abandon_at=FIXED_NOW + timedelta(days=1),
        retire_at=FIXED_NOW + timedelta(days=2),
    )

    cohort = load_human_reference_cohort(
        region="north",
        prestige_band="middle",
        low=2_000,
        high=8_000,
        now=FIXED_NOW,
    )

    assert cohort.local_sample_count == 1
    assert len(cohort.local_snapshots) == 1
    assert valid.id not in {stale.id, staff.id, virtual.id}
