from __future__ import annotations

import pytest

from core.exceptions import ForgeOperationError
from gameplay.services.buildings import forge_flow_helpers


def test_build_filtered_equipment_configs_respects_category():
    result = forge_flow_helpers.build_filtered_equipment_configs(
        equipment_config={
            "equip_helmet": {"category": "helmet"},
            "equip_dao": {"category": "dao"},
        },
        category="helmet",
    )

    assert result == [("equip_helmet", {"category": "helmet"})]


def test_validate_forging_quantity_rejects_out_of_range_values():
    with pytest.raises(ForgeOperationError, match="至少为1"):
        forge_flow_helpers.validate_forging_quantity(quantity=0, max_quantity=10)

    with pytest.raises(ForgeOperationError, match="单次最多锻造10件"):
        forge_flow_helpers.validate_forging_quantity(quantity=11, max_quantity=10)


def test_build_total_material_costs_multiplies_by_quantity():
    assert forge_flow_helpers.build_total_material_costs(materials={"tong": 3, "xi": 2}, quantity=4) == {
        "tong": 12,
        "xi": 8,
    }


def test_build_forging_quantity_text_formats_plural_suffix():
    assert forge_flow_helpers.build_forging_quantity_text(1) == ""
    assert forge_flow_helpers.build_forging_quantity_text(3) == "×3"
