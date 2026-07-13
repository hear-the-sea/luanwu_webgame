from __future__ import annotations

from gameplay.services.virtual_player_population import PopulationCell, plan_population_cells


def test_empty_cell_has_only_exploration_supply():
    cells = [PopulationCell("north", "veteran", 0, 0, 0, 0)]

    result = plan_population_cells(
        cells,
        cell_floor=10,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=100,
    )

    assert result.cells[0].target == 0
    assert result.target_total == 0


def test_active_and_search_cells_receive_demand_targets():
    cells = [
        PopulationCell("north", "junior", 3, 1, 1, 0),
        PopulationCell("south", "middle", 0, 0, 0, 8),
    ]

    result = plan_population_cells(
        cells,
        cell_floor=4,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=100,
    )

    assert [cell.target for cell in result.cells] == [6, 8]
    assert [cell.deficit for cell in result.cells] == [5, 8]


def test_existing_maintained_supply_keeps_a_cell_in_smooth_retirement():
    cells = [PopulationCell("west", "senior", 0, 3, 2, 0)]

    result = plan_population_cells(
        cells,
        cell_floor=2,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=100,
    )

    assert result.cells[0].target == 2
    assert result.cells[0].deficit == 0
    assert result.cells[0].excess == 1


def test_attackable_deficit_is_distinct_from_structural_deficit():
    cells = [PopulationCell("west", "senior", 1, 4, 1, 3)]

    result = plan_population_cells(
        cells,
        cell_floor=4,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=100,
    )

    cell = result.cells[0]
    assert cell.target == 4
    assert cell.structural_deficit == 0
    assert cell.attackable_target == 4
    assert cell.attackable_deficit == 3
    assert cell.deficit == 0


def test_attackable_target_is_capped_by_hard_cap_allocation():
    cells = [PopulationCell("west", "senior", 1, 4, 0, 8)]

    result = plan_population_cells(
        cells,
        cell_floor=4,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=3,
    )

    cell = result.cells[0]
    assert result.target_total == 3
    assert cell.attackable_target == 3
    assert cell.attackable_deficit == 3


def test_hard_cap_prioritizes_search_demand_then_active_cells():
    cells = [
        PopulationCell("north", "junior", 4, 0, 0, 0),
        PopulationCell("south", "middle", 0, 0, 0, 7),
    ]

    result = plan_population_cells(
        cells,
        cell_floor=4,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=8,
    )

    assert result.by_key[("south", "middle")].target == 7
    assert result.by_key[("north", "junior")].target == 1
    assert result.target_total == 8


def test_zero_hard_cap_means_no_global_cap():
    cells = [
        PopulationCell("north", "junior", 3, 0, 0, 0),
        PopulationCell("south", "middle", 0, 0, 0, 8),
    ]

    result = plan_population_cells(
        cells,
        cell_floor=4,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=0,
    )

    assert result.target_total == 14


def test_hard_cap_balances_cells_within_the_same_priority():
    cells = [
        PopulationCell("north", "junior", 2, 0, 0, 0),
        PopulationCell("south", "junior", 2, 0, 0, 0),
    ]

    result = plan_population_cells(
        cells,
        cell_floor=4,
        cell_multiplier=2,
        exploration_supply=0,
        hard_cap=4,
    )

    assert result.by_key[("north", "junior")].target == 2
    assert result.by_key[("south", "junior")].target == 2
