from __future__ import annotations

import gameplay.services.technology as tech_service
from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys
from gameplay.services.manor.core import calculate_building_capacity
from gameplay.services.technology_rules import allocate_upgrade_budget


def test_calculate_upgrade_cost_uses_budget_curve_for_all_templates(monkeypatch):
    monkeypatch.setattr(
        tech_service,
        "get_technology_template",
        lambda _key: {
            "base_cost": 100,
            "max_level": 4,
            "upgrade_cost_budget": 10000,
            "cost_curve": 1.2,
        },
    )

    costs = [tech_service.calculate_upgrade_cost("custom", level) for level in range(4)]
    assert sum(costs) == 10000
    assert costs[0] < costs[-1]


def test_calculate_upgrade_cost_uses_exact_total_budget_with_curve(monkeypatch):
    template = {
        "base_cost": 100,
        "max_level": 4,
        "upgrade_cost_budget": 10000,
        "cost_curve": 1.2,
    }
    monkeypatch.setattr(tech_service, "get_technology_template", lambda _key: template)

    costs = [tech_service.calculate_upgrade_cost("custom", level) for level in range(4)]

    assert sum(costs) == 10000
    assert costs[0] < costs[-1]
    assert all(costs[index] <= costs[index + 1] for index in range(len(costs) - 1))


def test_configured_technology_budgets_are_exact_and_scout_has_200_levels():
    tech_service.clear_technology_cache()
    templates = tech_service.load_technology_templates().get("technologies", []) or []
    configured = {str(template["key"]): template for template in templates if template.get("upgrade_cost_budget")}

    assert configured["scout_art"]["max_level"] == 200
    assert len(configured) == 42

    for tech_key, template in configured.items():
        max_level = int(template["max_level"])
        costs = [tech_service.calculate_upgrade_cost(tech_key, level) for level in range(max_level)]
        assert sum(costs) == int(template["upgrade_cost_budget"])
        schedule = allocate_upgrade_budget(
            total_budget=int(template["upgrade_time_budget"]),
            floor_amount=60,
            curve_growth=float(template["time_curve"]),
            step_count=max_level,
        )
        assert sum(schedule) == int(template["upgrade_time_budget"])

    tech_service.clear_technology_cache()


def test_all_technology_upgrade_costs_fit_within_max_silver_capacity():
    tech_service.clear_technology_cache()
    max_silver_capacity = calculate_building_capacity(
        BUILDING_MAX_LEVELS[BuildingKeys.SILVER_VAULT],
        is_silver_vault=True,
    )
    violations: list[str] = []

    for template in tech_service.load_technology_templates().get("technologies", []) or []:
        if not isinstance(template, dict):
            continue
        tech_key = str(template.get("key") or "").strip()
        if not tech_key:
            continue
        max_level = max(0, int(template.get("max_level", 0) or 0))
        for current_level in range(max_level):
            cost = tech_service.calculate_upgrade_cost(tech_key, current_level)
            if cost > max_silver_capacity:
                violations.append(f"{tech_key} {current_level}->{current_level + 1}: {cost}")

    tech_service.clear_technology_cache()
    assert violations == []
