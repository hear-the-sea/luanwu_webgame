from types import SimpleNamespace

import pytest

from battle.simulation.utils import summarize_losses
from battle.utils import battle_calculator


def test_defeated_troop_loss_rate_is_80_percent_for_both_sides(monkeypatch):
    observed_rates: list[float] = []
    monkeypatch.setattr(
        battle_calculator,
        "binomial_sample",
        lambda _sample_size, probability, _rng: observed_rates.append(probability) or 0,
    )
    troop = SimpleNamespace(
        name="测试护院",
        template_key="test_troop",
        kind="troop",
        max_hp=100,
        hp=0,
        initial_troop_strength=10,
        troop_strength=0,
    )

    attacker_losses = battle_calculator.calculate_team_losses([troop], False, object(), side="attacker")
    defender_losses = battle_calculator.calculate_team_losses([troop], False, object(), side="defender")

    assert attacker_losses["troop_loss_rate"] == 0.8
    assert defender_losses["troop_loss_rate"] == 0.8
    assert observed_rates == [0.8, 0.8]


@pytest.mark.parametrize(
    ("player_side", "expected_rates", "expected_losses"),
    [
        ("attacker", [0.5, 0.8], (5, 8)),
        ("defender", [0.8, 0.5], (8, 5)),
    ],
)
def test_player_mission_troop_loss_rate_only_changes_player_casualties(
    monkeypatch, player_side, expected_rates, expected_losses
):
    observed_rates: list[float] = []

    def _sample(sample_size, probability, _rng):
        observed_rates.append(probability)
        return int(sample_size * probability)

    monkeypatch.setattr(battle_calculator, "binomial_sample", _sample)
    troop = SimpleNamespace(
        name="测试护院",
        template_key="test_troop",
        kind="troop",
        max_hp=100,
        hp=0,
        initial_troop_strength=10,
        troop_strength=0,
    )

    losses = summarize_losses(
        [troop],
        [troop],
        "defender",
        object(),
        attacker_troop_loss_rate=0.5 if player_side == "attacker" else None,
        defender_troop_loss_rate=0.5 if player_side == "defender" else None,
    )

    assert observed_rates == expected_rates
    assert losses["attacker"]["troops_lost"] == expected_losses[0]
    assert losses["defender"]["troops_lost"] == expected_losses[1]
    assert losses[player_side]["troop_loss_rate"] == 0.5
    assert losses[player_side]["troops_remaining"] == 5
