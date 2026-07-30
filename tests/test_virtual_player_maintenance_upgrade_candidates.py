from __future__ import annotations

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from gameplay.constants import BuildingKeys
from gameplay.models import Building, Manor
from gameplay.services.manor.core import apply_building_upgrade_locked, quote_building_upgrade
from gameplay.services.technology import apply_technology_upgrade_locked, quote_technology_upgrade
from gameplay.services.virtual_player_core.maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    TechnologyUpgradeActionSpec,
)
from gameplay.services.virtual_player_core.maintenance_upgrade_candidates import (
    build_building_upgrade_candidates,
    build_technology_upgrade_candidates,
)
from gameplay.services.virtual_player_core.reference_snapshots import load_manor_strength_summary
from gameplay.services.virtual_player_core.strategy import BotDevelopmentPlan


def _development_plan(
    *,
    building_focuses: tuple[str, ...] = (BuildingKeys.SILVER_VAULT,),
    technology_focuses: tuple[str, ...] = ("march_art",),
) -> BotDevelopmentPlan:
    return BotDevelopmentPlan(
        schema_version=1,
        optimization_bias=1.0,
        inertia_bias=0.5,
        roster_focus=0.5,
        preferred_guest_archetypes=("military",),
        primary_troop_class="dao",
        secondary_troop_class="qiang",
        troop_mix=(("dao", 0.6), ("qiang", 0.4)),
        preferred_gear_stats=("force",),
        preferred_skill_kinds=("active",),
        building_focuses=building_focuses,
        technology_focuses=technology_focuses,
    )


def _fund(manor: Manor) -> None:
    Manor.objects.filter(pk=manor.pk).update(
        silver=1_000_000,
        grain=1_000_000,
        silver_capacity=1_000_000,
        grain_capacity=1_000_000,
        prestige=3,
        prestige_silver_spent=900,
    )
    manor.refresh_from_db()


@pytest.mark.django_db
def test_building_upgrade_candidate_is_pure_and_matches_committed_strength(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="maintenance_building_candidate")
    _fund(manor)
    building = manor.buildings.select_related("building_type").get(
        building_type__key=BuildingKeys.SILVER_VAULT,
    )
    quote = quote_building_upgrade(manor, building)
    strength_before = load_manor_strength_summary(manor_id=manor.id)

    with CaptureQueriesContext(connection) as captured:
        candidates, specs = build_building_upgrade_candidates(
            manor=manor,
            prestige_band="newbie",
            strength_before=strength_before,
            development_plan=_development_plan(),
            quotes=(quote,),
            prestige_band_for=lambda _prestige: "newbie",
        )

    assert captured.captured_queries == []
    assert len(candidates) == 1
    intent = candidates[0]
    spec = specs[intent.business_key]
    assert isinstance(spec, BuildingUpgradeActionSpec)
    assert spec.level_after == spec.level_before + 1
    assert spec.resource_costs == quote.resource_cost

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        locked_building = Building.objects.select_for_update().select_related("building_type").get(pk=building.pk)
        apply_building_upgrade_locked(
            locked_manor,
            locked_building,
            quote,
            sync_production=False,
        )

    assert load_manor_strength_summary(manor_id=manor.id) == intent.strength_after


@pytest.mark.django_db
def test_technology_upgrade_candidate_is_pure_and_matches_committed_strength(
    manor_factory,
) -> None:
    manor, _user = manor_factory(username="maintenance_technology_candidate")
    _fund(manor)
    quote = quote_technology_upgrade(manor, "march_art")
    strength_before = load_manor_strength_summary(manor_id=manor.id)

    with CaptureQueriesContext(connection) as captured:
        candidates, specs = build_technology_upgrade_candidates(
            manor=manor,
            prestige_band="newbie",
            strength_before=strength_before,
            development_plan=_development_plan(),
            quotes=(quote,),
            prestige_band_for=lambda _prestige: "newbie",
        )

    assert captured.captured_queries == []
    assert len(candidates) == 1
    intent = candidates[0]
    spec = specs[intent.business_key]
    assert isinstance(spec, TechnologyUpgradeActionSpec)
    assert spec.level_before == 0
    assert spec.level_after == 1
    assert spec.resource_costs == (("silver", quote.silver_cost),)

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        apply_technology_upgrade_locked(
            locked_manor,
            quote,
            sync_production=False,
        )

    assert load_manor_strength_summary(manor_id=manor.id) == intent.strength_after


@pytest.mark.django_db
def test_upgrade_candidates_reject_unaffordable_quotes(manor_factory) -> None:
    manor, _user = manor_factory(username="maintenance_upgrade_unaffordable")
    Manor.objects.filter(pk=manor.pk).update(silver=0, grain=0)
    manor.refresh_from_db()
    building = manor.buildings.select_related("building_type").get(
        building_type__key=BuildingKeys.SILVER_VAULT,
    )
    building_quote = quote_building_upgrade(manor, building)
    technology_quote = quote_technology_upgrade(manor, "march_art")
    strength = load_manor_strength_summary(manor_id=manor.id)
    plan = _development_plan()

    building_candidates, _building_specs = build_building_upgrade_candidates(
        manor=manor,
        prestige_band="newbie",
        strength_before=strength,
        development_plan=plan,
        quotes=(building_quote,),
        prestige_band_for=lambda _prestige: "newbie",
    )
    technology_candidates, _technology_specs = build_technology_upgrade_candidates(
        manor=manor,
        prestige_band="newbie",
        strength_before=strength,
        development_plan=plan,
        quotes=(technology_quote,),
        prestige_band_for=lambda _prestige: "newbie",
    )

    assert building_candidates == ()
    assert technology_candidates == ()
