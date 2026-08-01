import pytest

from guests.utils.training_calculator import calculate_level_up_cost, calculate_training_duration


def test_calculate_level_up_cost_requires_positive_current_level():
    with pytest.raises(AssertionError, match="当前等级必须>=1"):
        calculate_level_up_cost(0, 1)


def test_calculate_level_up_cost_requires_positive_target_levels():
    with pytest.raises(AssertionError, match="升级等级数必须>=1"):
        calculate_level_up_cost(1, 0)


def test_calculate_level_up_cost_rejects_levels_above_cap():
    with pytest.raises(AssertionError, match="已达等级上限"):
        calculate_level_up_cost(99, 2)


def test_calculate_training_duration_requires_positive_current_level():
    with pytest.raises(AssertionError, match="当前等级必须>=1"):
        calculate_training_duration(0, "black", 1)


def test_calculate_training_duration_requires_positive_levels():
    with pytest.raises(AssertionError, match="训练等级数必须>=1"):
        calculate_training_duration(1, "black", 0)


def test_calculate_training_duration_uses_exponential_growth(monkeypatch):
    monkeypatch.setattr("guests.utils.training_calculator.scale_duration", lambda total, minimum=1: total)

    assert calculate_training_duration(1, "black", 1) == 1800
    assert calculate_training_duration(1, "purple", 1) == 2700
    assert calculate_training_duration(30, "green", 1) == 15366
    assert calculate_training_duration(99, "green", 1) == 1637057
