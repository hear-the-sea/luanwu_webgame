from __future__ import annotations

from collections.abc import Callable

from gameplay.models import Manor
from gameplay.services.manor.core import BuildingUpgradeQuote
from gameplay.services.technology import TechnologyUpgradeQuote

from .maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    MaintenanceActionSpec,
    TechnologyUpgradeActionSpec,
    project_maintenance_action_intent,
)
from .projection import DevelopmentIntent, StrengthSummary
from .reference_snapshots import CORE_BUILDING_KEYS
from .strategy import BotDevelopmentPlan


class MaintenanceUpgradeCandidateError(ValueError):
    pass


def _project_prestige_after(manor: Manor, *, silver_cost: int) -> int:
    if silver_cost < 0:
        raise MaintenanceUpgradeCandidateError("upgrade silver cost must be non-negative")
    spent_before = int(manor.prestige_silver_spent or 0)
    spending_prestige_before = spent_before // 1_000
    pvp_prestige = max(0, int(manor.prestige or 0) - spending_prestige_before)
    return (spent_before + silver_cost) // 1_000 + pvp_prestige


def _has_resources(manor: Manor, costs: tuple[tuple[str, int], ...]) -> bool:
    for resource, amount in costs:
        available = getattr(manor, resource, None)
        if available is None:
            raise MaintenanceUpgradeCandidateError(f"unsupported upgrade resource: {resource}")
        if int(available or 0) < int(amount):
            return False
    return True


def _project_strength(
    before: StrengthSummary,
    *,
    prestige_after: int,
    core_building_level_after: int | None = None,
) -> StrengthSummary:
    components = dict(before.components)
    components["prestige"] = float(prestige_after)
    if core_building_level_after is not None:
        components["core_building_level"] = float(core_building_level_after)
    return StrengthSummary(composite=before.composite, components=components)


def _resolved_target_band(
    prestige_after: int,
    *,
    prestige_band_for: Callable[[int], str | None],
) -> str:
    target_band = prestige_band_for(prestige_after)
    if not isinstance(target_band, str) or not target_band.strip():
        raise MaintenanceUpgradeCandidateError("upgrade target prestige does not belong to a configured band")
    return target_band.strip()


def build_building_upgrade_candidates(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    quotes: tuple[BuildingUpgradeQuote, ...],
    prestige_band_for: Callable[[int], str | None],
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, MaintenanceActionSpec],
]:
    focus_rank = {key: index for index, key in enumerate(development_plan.building_focuses)}
    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    for quote in sorted(quotes, key=lambda value: (value.building_key, value.building_id)):
        if quote.manor_id != int(manor.pk):
            raise MaintenanceUpgradeCandidateError("building upgrade quote belongs to another Manor")
        if quote.building_key not in focus_rank or not _has_resources(
            manor,
            quote.resource_cost,
        ):
            continue
        silver_cost = dict(quote.resource_cost).get("silver", 0)
        prestige_after = _project_prestige_after(manor, silver_cost=silver_cost)
        core_before = int(strength_before.components["core_building_level"])
        core_after = max(core_before, quote.target_level) if quote.building_key in CORE_BUILDING_KEYS else core_before
        spec = BuildingUpgradeActionSpec(
            building_id=quote.building_id,
            building_key=quote.building_key,
            level_before=quote.current_level,
            level_after=quote.target_level,
            resource_costs=quote.resource_cost,
            prestige_after=prestige_after,
            core_building_level_after=core_after,
        )
        strength_after = _project_strength(
            strength_before,
            prestige_after=prestige_after,
            core_building_level_after=core_after,
        )
        focus_weight = 1.0 + (len(focus_rank) - focus_rank[quote.building_key]) / max(1, len(focus_rank))
        structural_gain = max(0, core_after - core_before)
        prestige_gain = max(
            0,
            prestige_after - int(strength_before.components["prestige"]),
        )
        utility_score = (focus_weight + 2.0 * structural_gain + float(prestige_gain)) / max(
            1, sum(amount for _resource, amount in quote.resource_cost)
        )
        intent = project_maintenance_action_intent(
            spec=spec,
            source_prestige_band=prestige_band,
            target_prestige_band=_resolved_target_band(
                prestige_after,
                prestige_band_for=prestige_band_for,
            ),
            strength_before=strength_before,
            strength_after=strength_after,
            utility_score=utility_score,
        )
        candidates.append(intent)
        specs[intent.business_key] = spec
    return tuple(candidates), specs


def build_technology_upgrade_candidates(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    quotes: tuple[TechnologyUpgradeQuote, ...],
    prestige_band_for: Callable[[int], str | None],
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, MaintenanceActionSpec],
]:
    focus_rank = {key: index for index, key in enumerate(development_plan.technology_focuses)}
    candidates: list[DevelopmentIntent] = []
    specs: dict[str, MaintenanceActionSpec] = {}
    for quote in sorted(quotes, key=lambda value: value.technology_key):
        if quote.manor_id != int(manor.pk):
            raise MaintenanceUpgradeCandidateError("technology upgrade quote belongs to another Manor")
        if quote.technology_key not in focus_rank or int(manor.silver or 0) < quote.silver_cost:
            continue
        prestige_after = _project_prestige_after(
            manor,
            silver_cost=quote.silver_cost,
        )
        spec = TechnologyUpgradeActionSpec(
            technology_key=quote.technology_key,
            level_before=quote.current_level,
            level_after=quote.target_level,
            resource_costs=(("silver", quote.silver_cost),),
            prestige_after=prestige_after,
        )
        strength_after = _project_strength(
            strength_before,
            prestige_after=prestige_after,
        )
        focus_weight = 1.0 + (len(focus_rank) - focus_rank[quote.technology_key]) / max(1, len(focus_rank))
        prestige_gain = max(
            0,
            prestige_after - int(strength_before.components["prestige"]),
        )
        utility_score = (focus_weight + float(prestige_gain)) / max(
            1,
            quote.silver_cost,
        )
        intent = project_maintenance_action_intent(
            spec=spec,
            source_prestige_band=prestige_band,
            target_prestige_band=_resolved_target_band(
                prestige_after,
                prestige_band_for=prestige_band_for,
            ),
            strength_before=strength_before,
            strength_after=strength_after,
            utility_score=utility_score,
        )
        candidates.append(intent)
        specs[intent.business_key] = spec
    return tuple(candidates), specs


__all__ = [
    "MaintenanceUpgradeCandidateError",
    "build_building_upgrade_candidates",
    "build_technology_upgrade_candidates",
]
