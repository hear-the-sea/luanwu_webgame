from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from gameplay.services.virtual_player_core.inventory_budget import inventory_daily_cap_limits
from gameplay.services.virtual_player_core.maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    EquipmentEquipActionSpec,
    InventoryAcquisitionActionSpec,
    MaintenanceActionSpecError,
    SkillLearningActionSpec,
    TechnologyUpgradeActionSpec,
    maintenance_action_spec_payload,
    project_maintenance_action_intent,
)
from gameplay.services.virtual_player_core.projection import StrengthSummary

BASE_STRENGTH = StrengthSummary(
    composite=140,
    components={
        "arena_lineup_power": 100,
        "core_building_level": 4,
        "guest_count": 2,
        "max_guest_level": 8,
        "prestige": 90,
        "troop_total": 20,
    },
)


def _strength(**changes: int) -> StrengthSummary:
    components = dict(BASE_STRENGTH.components)
    components.update(changes)
    return StrengthSummary(
        composite=float(components["arena_lineup_power"] + 2 * components["troop_total"]),
        components=components,
    )


def test_action_specs_freeze_business_keys_and_canonical_payloads() -> None:
    skill = SkillLearningActionSpec(
        guest_id=7,
        inventory_item_id=11,
        item_template_id=13,
        item_key="book_alpha",
        item_quantity_before=2,
        skill_id=17,
        skill_key="alpha",
    )
    equipment = EquipmentEquipActionSpec(
        guest_id=7,
        inventory_item_id=19,
        item_template_id=23,
        item_key="equip_alpha",
        item_quantity_before=1,
        slot="weapon",
    )
    inventory = InventoryAcquisitionActionSpec(
        item_template_id=29,
        item_key="medicine_alpha",
        daily_caps=(("rare", 8),),
    )
    building = BuildingUpgradeActionSpec(
        building_id=31,
        building_key="silver_vault",
        level_before=4,
        level_after=5,
        resource_costs=(("grain", 20), ("silver", 10)),
        prestige_after=91,
        core_building_level_after=5,
    )
    technology = TechnologyUpgradeActionSpec(
        technology_key="architecture",
        level_before=0,
        level_after=1,
        resource_costs=(("silver", 8_000),),
        prestige_after=98,
    )

    assert skill.business_key == "skill_learning:guest:7:skill:alpha"
    assert equipment.business_key == "equipment_equip:guest:7:item:equip_alpha"
    assert inventory.business_key == "inventory_acquisition:item:medicine_alpha"
    assert building.business_key == "building_upgrade:silver_vault:4->5"
    assert technology.business_key == "technology_upgrade:architecture:0->1"
    assert maintenance_action_spec_payload(equipment) == {
        "action_kind": "equipment_equip",
        "business_key": "equipment_equip:guest:7:item:equip_alpha",
        "guest_id": 7,
        "inventory_item_id": 19,
        "item_key": "equip_alpha",
        "item_quantity_before": 1,
        "item_template_id": 23,
        "slot": "weapon",
    }
    assert maintenance_action_spec_payload(building) == {
        "action_kind": "building_upgrade",
        "building_id": 31,
        "building_key": "silver_vault",
        "business_key": "building_upgrade:silver_vault:4->5",
        "core_building_level_after": 5,
        "level_after": 5,
        "level_before": 4,
        "prestige_after": 91,
        "resource_costs": {"grain": 20, "silver": 10},
    }
    assert maintenance_action_spec_payload(None) is None


def test_rare_powerful_inventory_caps_use_canonical_action_spec_order() -> None:
    daily_caps = inventory_daily_cap_limits(
        SimpleNamespace(rarity="purple", price=100_000),
        config={
            "projection": {
                "rare_item_daily_global_cap": 8,
                "powerful_item_daily_global_cap": 2,
                "powerful_item_min_price": 100_000,
            }
        },
    )

    assert daily_caps == (("powerful", 2), ("rare", 8))
    assert (
        InventoryAcquisitionActionSpec(
            item_template_id=1,
            item_key="rare_powerful_item",
            daily_caps=daily_caps,
        ).daily_caps
        == daily_caps
    )


@pytest.mark.parametrize(
    "factory, message",
    [
        (
            lambda: InventoryAcquisitionActionSpec(
                item_template_id=True,
                item_key="item",
                daily_caps=(),
            ),
            "positive integer",
        ),
        (
            lambda: BuildingUpgradeActionSpec(
                building_id=1,
                building_key="vault",
                level_before=2,
                level_after=4,
                resource_costs=(("silver", 1),),
                prestige_after=0,
                core_building_level_after=4,
            ),
            "exactly one level",
        ),
        (
            lambda: TechnologyUpgradeActionSpec(
                technology_key="architecture",
                level_before=0,
                level_after=1,
                resource_costs=(("silver", 1), ("grain", 1)),
                prestige_after=0,
            ),
            "canonical resource order",
        ),
    ],
)
def test_action_specs_fail_closed_on_invalid_frozen_metadata(
    factory,
    message: str,
) -> None:
    with pytest.raises(MaintenanceActionSpecError, match=message):
        factory()


def test_skill_and_inventory_projection_must_be_strength_neutral() -> None:
    skill = SkillLearningActionSpec(
        guest_id=1,
        inventory_item_id=2,
        item_template_id=3,
        item_key="book_alpha",
        item_quantity_before=1,
        skill_id=4,
        skill_key="alpha",
    )
    inventory = InventoryAcquisitionActionSpec(
        item_template_id=5,
        item_key="medicine_alpha",
        daily_caps=(),
    )

    for spec in (skill, inventory):
        intent = project_maintenance_action_intent(
            spec=spec,
            source_prestige_band="settler",
            target_prestige_band="settler",
            strength_before=BASE_STRENGTH,
            strength_after=BASE_STRENGTH,
            utility_score=1.0,
        )
        assert intent.business_key == spec.business_key
        assert intent.action_kind == spec.action_kind

        with pytest.raises(MaintenanceActionSpecError, match="frozen strength"):
            project_maintenance_action_intent(
                spec=spec,
                source_prestige_band="settler",
                target_prestige_band="settler",
                strength_before=BASE_STRENGTH,
                strength_after=_strength(arena_lineup_power=101),
                utility_score=1.0,
            )


def test_equipment_projection_only_allows_non_decreasing_lineup_power() -> None:
    spec = EquipmentEquipActionSpec(
        guest_id=1,
        inventory_item_id=2,
        item_template_id=3,
        item_key="equip_alpha",
        item_quantity_before=1,
        slot="weapon",
    )

    intent = project_maintenance_action_intent(
        spec=spec,
        source_prestige_band="settler",
        target_prestige_band="settler",
        strength_before=BASE_STRENGTH,
        strength_after=_strength(arena_lineup_power=110),
        utility_score=1.0,
    )

    assert intent.strength_after.components["arena_lineup_power"] == 110
    with pytest.raises(MaintenanceActionSpecError, match="must not reduce"):
        project_maintenance_action_intent(
            spec=spec,
            source_prestige_band="settler",
            target_prestige_band="settler",
            strength_before=BASE_STRENGTH,
            strength_after=_strength(arena_lineup_power=99),
            utility_score=1.0,
        )
    with pytest.raises(MaintenanceActionSpecError, match="unsupported"):
        project_maintenance_action_intent(
            spec=spec,
            source_prestige_band="settler",
            target_prestige_band="settler",
            strength_before=BASE_STRENGTH,
            strength_after=_strength(core_building_level=5),
            utility_score=1.0,
        )


def test_building_and_technology_projection_match_their_frozen_specs() -> None:
    building = BuildingUpgradeActionSpec(
        building_id=1,
        building_key="silver_vault",
        level_before=4,
        level_after=5,
        resource_costs=(("silver", 10),),
        prestige_after=91,
        core_building_level_after=5,
    )
    technology = TechnologyUpgradeActionSpec(
        technology_key="architecture",
        level_before=0,
        level_after=1,
        resource_costs=(("silver", 10),),
        prestige_after=91,
    )

    building_intent = project_maintenance_action_intent(
        spec=building,
        source_prestige_band="settler",
        target_prestige_band="settler",
        strength_before=BASE_STRENGTH,
        strength_after=_strength(core_building_level=5, prestige=91),
        utility_score=1.0,
    )
    technology_intent = project_maintenance_action_intent(
        spec=technology,
        source_prestige_band="settler",
        target_prestige_band="settler",
        strength_before=BASE_STRENGTH,
        strength_after=_strength(prestige=91),
        utility_score=1.0,
    )

    assert building_intent.action_kind == "building_upgrade"
    assert technology_intent.action_kind == "technology_upgrade"
    with pytest.raises(MaintenanceActionSpecError, match="does not match"):
        project_maintenance_action_intent(
            spec=replace(building, core_building_level_after=6),
            source_prestige_band="settler",
            target_prestige_band="settler",
            strength_before=BASE_STRENGTH,
            strength_after=_strength(core_building_level=5, prestige=91),
            utility_score=1.0,
        )
