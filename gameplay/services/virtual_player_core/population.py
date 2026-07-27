from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PopulationCell:
    region: str
    prestige_band: str
    active_real: int
    maintained_supply: int
    attackable_supply: int
    search_demand: int


@dataclass(frozen=True)
class PlannedPopulationCell:
    region: str
    prestige_band: str
    active_real: int
    maintained_supply: int
    attackable_supply: int
    search_demand: int
    target: int

    @property
    def structural_deficit(self) -> int:
        return max(0, int(self.target) - int(self.maintained_supply))

    @property
    def attackable_target(self) -> int:
        observed_need = int(self.attackable_supply) + int(self.search_demand)
        return max(0, min(int(self.target), observed_need))

    @property
    def attackable_deficit(self) -> int:
        return max(0, self.attackable_target - int(self.attackable_supply))

    @property
    def deficit(self) -> int:
        """Backward-compatible alias for the structural creation deficit."""
        return self.structural_deficit

    @property
    def excess(self) -> int:
        return max(0, int(self.maintained_supply) - int(self.target))


@dataclass(frozen=True)
class PopulationPlan:
    cells: tuple[PlannedPopulationCell, ...]
    hard_cap: int
    region_target_rows: tuple[tuple[str, int], ...] = ()

    @property
    def target_total(self) -> int:
        return sum(cell.target for cell in self.cells)

    @property
    def by_key(self) -> dict[tuple[str, str], PlannedPopulationCell]:
        return {(cell.region, cell.prestige_band): cell for cell in self.cells}

    @property
    def region_targets(self) -> dict[str, int]:
        if self.region_target_rows:
            return dict(self.region_target_rows)
        targets: dict[str, int] = {}
        for cell in self.cells:
            targets[cell.region] = targets.get(cell.region, 0) + int(cell.target)
        return targets


def _normalized_cell(cell: PopulationCell) -> PopulationCell:
    return PopulationCell(
        region=str(cell.region),
        prestige_band=str(cell.prestige_band),
        active_real=max(0, int(cell.active_real)),
        maintained_supply=max(0, int(cell.maintained_supply)),
        attackable_supply=max(0, int(cell.attackable_supply)),
        search_demand=max(0, int(cell.search_demand)),
    )


def plan_population_cells(
    cells: list[PopulationCell],
    *,
    cell_floor: int | None = None,
    cell_multiplier: int | None = None,
    exploration_supply: int = 0,
    hard_cap: int | None = None,
    region_floor: int | None = None,
    region_multiplier: int | None = None,
    global_floor: int = 0,
    global_multiplier: int = 0,
    entry_band: str = "newbie",
    hard_cap_override: int | None = None,
) -> PopulationPlan:
    normalized = [_normalized_cell(cell) for cell in cells]

    if region_floor is not None or region_multiplier is not None:
        floor = max(0, int(region_floor or 0))
        multiplier = max(0, int(region_multiplier or 0))
        total_active = sum(cell.active_real for cell in normalized)
        dynamic_cap = max(max(0, int(global_floor)), total_active * max(0, int(global_multiplier)))
        cap = dynamic_cap if hard_cap_override is None else max(0, int(hard_cap_override))

        indexes_by_region: dict[str, list[int]] = {}
        for index, cell in enumerate(normalized):
            indexes_by_region.setdefault(cell.region, []).append(index)

        desired_by_region: dict[str, int] = {}
        cell_limits: dict[int, int] = {}
        for region, indexes in indexes_by_region.items():
            for index in indexes:
                cell = normalized[index]
                cell_limits[index] = max(cell.active_real * multiplier, cell.search_demand)
            desired_by_region[region] = max(floor, sum(cell_limits[index] for index in indexes))

        region_allocated = {region: 0 for region in indexes_by_region}
        remaining_cap = cap
        while remaining_cap > 0:
            region_candidates = [
                region for region, desired in desired_by_region.items() if region_allocated[region] < desired
            ]
            if not region_candidates:
                break
            region = min(
                region_candidates,
                key=lambda item: (-(desired_by_region[item] - region_allocated[item]), item),
            )
            region_allocated[region] += 1
            remaining_cap -= 1

        allocated = [0 for _cell in normalized]
        for region, indexes in indexes_by_region.items():
            budget = region_allocated[region]
            while budget > 0:
                cell_candidates = [index for index in indexes if allocated[index] < cell_limits[index]]
                if not cell_candidates:
                    break
                index = min(
                    cell_candidates,
                    key=lambda item: (-(cell_limits[item] - allocated[item]), item),
                )
                allocated[index] += 1
                budget -= 1
            if budget > 0:
                entry_indexes = [index for index in indexes if normalized[index].prestige_band == entry_band]
                fallback_index = entry_indexes[0] if entry_indexes else indexes[0]
                allocated[fallback_index] += budget

        planned = tuple(
            PlannedPopulationCell(
                region=cell.region,
                prestige_band=cell.prestige_band,
                active_real=cell.active_real,
                maintained_supply=cell.maintained_supply,
                attackable_supply=cell.attackable_supply,
                search_demand=cell.search_demand,
                target=allocated[index],
            )
            for index, cell in enumerate(normalized)
        )
        return PopulationPlan(
            cells=planned,
            hard_cap=cap,
            region_target_rows=tuple(sorted(region_allocated.items())),
        )

    assert cell_floor is not None
    assert cell_multiplier is not None
    assert hard_cap is not None
    floor = max(0, int(cell_floor))
    multiplier = max(0, int(cell_multiplier))
    exploration = max(0, int(exploration_supply))
    cap = max(0, int(hard_cap))

    desired: list[int] = []
    for cell in normalized:
        has_demand = cell.active_real > 0 or cell.search_demand > 0 or cell.maintained_supply > 0
        desired.append(max(floor, cell.active_real * multiplier, cell.search_demand) if has_demand else exploration)

    if cap <= 0 or sum(desired) <= cap:
        allocated = desired
    else:
        allocated = [0 for _cell in normalized]
        remaining_cap = cap

        def allocate(limits: dict[int, int]) -> None:
            nonlocal remaining_cap
            while remaining_cap > 0:
                candidates = [index for index, limit in limits.items() if allocated[index] < limit]
                if not candidates:
                    return
                index = min(
                    candidates,
                    key=lambda item: (
                        -(limits[item] - allocated[item]),
                        normalized[item].region,
                        normalized[item].prestige_band,
                    ),
                )
                allocated[index] += 1
                remaining_cap -= 1

        allocate(
            {
                index: min(desired[index], cell.search_demand)
                for index, cell in enumerate(normalized)
                if cell.search_demand > 0
            }
        )
        allocate({index: desired[index] for index, cell in enumerate(normalized) if cell.active_real > 0})
        allocate({index: desired[index] for index, cell in enumerate(normalized) if cell.maintained_supply > 0})
        allocate({index: desired[index] for index in range(len(normalized))})

    planned = tuple(
        PlannedPopulationCell(
            region=cell.region,
            prestige_band=cell.prestige_band,
            active_real=cell.active_real,
            maintained_supply=cell.maintained_supply,
            attackable_supply=cell.attackable_supply,
            search_demand=cell.search_demand,
            target=allocated[index],
        )
        for index, cell in enumerate(normalized)
    )
    return PopulationPlan(cells=planned, hard_cap=cap)
