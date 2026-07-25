from __future__ import annotations

from types import SimpleNamespace

import pytest

from gameplay.services.work_requirements import evaluate_work_requirements, get_enabled_work_requirements


def _guest(**overrides):
    values = {
        "level": 20,
        "force": 100,
        "intellect": 100,
        "defense_stat": 100,
        "agility": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _work(**overrides):
    values = {
        "required_level": 1,
        "required_force": 0,
        "required_intellect": 0,
        "required_defense": 0,
        "required_agility": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_enabled_work_requirements_keep_fixed_order_and_ignore_zero_attributes():
    work = _work(
        required_level=16,
        required_force=0,
        required_intellect=105,
        required_defense=0,
        required_agility=60,
    )

    requirements = get_enabled_work_requirements(work)

    assert [(item.key, item.label, item.required) for item in requirements] == [
        ("level", "等级", 16),
        ("intellect", "智力", 105),
        ("agility", "敏捷", 60),
    ]


def test_evaluate_work_requirements_returns_every_missing_requirement_in_fixed_order():
    guest = _guest(level=15, force=99, intellect=104, defense_stat=49, agility=59)
    work = _work(
        required_level=16,
        required_force=100,
        required_intellect=105,
        required_defense=50,
        required_agility=60,
    )

    result = evaluate_work_requirements(guest, work)

    assert [item.key for item in result.requirements] == ["level", "force", "intellect", "defense", "agility"]
    assert [(item.key, item.actual, item.required, item.missing) for item in result.missing_requirements] == [
        ("level", 15, 16, 1),
        ("force", 99, 100, 1),
        ("intellect", 104, 105, 1),
        ("defense", 49, 50, 1),
        ("agility", 59, 60, 1),
    ]
    assert result.requirements_met is False
    assert result.level_missing == 1
    assert result.attribute_missing == 4


@pytest.mark.parametrize(
    ("required_field", "guest_field", "requirement_key"),
    [
        ("required_force", "force", "force"),
        ("required_intellect", "intellect", "intellect"),
        ("required_defense", "defense_stat", "defense"),
        ("required_agility", "agility", "agility"),
    ],
)
@pytest.mark.parametrize(("actual", "expected_met", "expected_missing"), [(49, False, 1), (50, True, 0), (51, True, 0)])
def test_each_attribute_uses_current_value_and_accepts_exact_threshold(
    required_field,
    guest_field,
    requirement_key,
    actual,
    expected_met,
    expected_missing,
):
    result = evaluate_work_requirements(
        _guest(**{guest_field: actual}),
        _work(**{required_field: 50}),
    )

    attribute_result = result.requirements[1]
    assert attribute_result.key == requirement_key
    assert attribute_result.actual == actual
    assert attribute_result.missing == expected_missing
    assert result.requirements_met is expected_met


def test_dual_attribute_requirements_use_and_semantics():
    work = _work(required_level=16, required_intellect=105, required_agility=60)

    intellect_short = evaluate_work_requirements(_guest(level=16, intellect=104, agility=60), work)
    agility_short = evaluate_work_requirements(_guest(level=16, intellect=105, agility=59), work)
    exact = evaluate_work_requirements(_guest(level=16, intellect=105, agility=60), work)

    assert intellect_short.requirements_met is False
    assert agility_short.requirements_met is False
    assert exact.requirements_met is True


def test_eligibility_tracks_attribute_surplus_without_mixing_in_level():
    result = evaluate_work_requirements(
        _guest(level=30, force=110, intellect=130),
        _work(required_level=10, required_force=100, required_intellect=105),
    )

    assert result.level_missing == 0
    assert result.attribute_missing == 0
    assert result.attribute_surplus == 35
