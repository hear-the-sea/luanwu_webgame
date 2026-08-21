from types import SimpleNamespace

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
