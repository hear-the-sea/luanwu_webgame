from types import SimpleNamespace

import pytest

from gameplay.services.raid import scout


@pytest.mark.parametrize(
    "attacker_level,defender_level,expected",
    [
        (1, 100, 0.01),
        (0, 200, 0.01),
        (1, 49, 0.02),
        (1, 50, 0.01),
        (1, 51, 0.01),
        (100, 100, 0.50),
        (100, 101, 0.49),
        (101, 100, 0.51),
        (144, 100, 0.94),
        (145, 100, 0.95),
        (200, 1, 0.95),
    ],
)
def test_scout_success_rate_level_difference_and_limits(monkeypatch, attacker_level, defender_level, expected):
    attacker = SimpleNamespace(level=attacker_level)
    defender = SimpleNamespace(level=defender_level)
    monkeypatch.setattr(scout, "get_scout_tech_level", lambda manor: manor.level)
    assert scout.calculate_scout_success_rate(attacker, defender) == pytest.approx(expected)
