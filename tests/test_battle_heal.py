import random
from types import SimpleNamespace

from battle.status_manager import apply_battle_heal


def make_troop(**kwargs):
    defaults = {
        "name": "Tester",
        "side": "attacker",
        "kind": "troop",
        "troop_class": "quan",
        "hp": 1000,
        "max_hp": 2000,
        "troop_strength": 50,
        "initial_troop_strength": 100,
        "unit_hp": 10,
        "tech_effects": {"battle_heal_chance": 1.0, "battle_heal_amount": 0.10},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_quan_battle_heal_defender_recovers_13_percent():
    rng = random.Random(0)
    defender = make_troop(side="defender")

    heals = apply_battle_heal([defender], rng)

    assert len(heals) == 1
    assert heals[0]["healed"] == 130  # 1000 * 13%
    assert defender.hp == 1130
    assert defender.troop_strength == 63
    assert heals[0]["unit_state"]["current"] == 63
    assert heals[0]["unit_state"]["maximum"] == 100
    assert heals[0]["unit_state"]["percent"] == 63


def test_quan_battle_heal_attacker_remains_10_percent():
    rng = random.Random(0)
    attacker = make_troop(side="attacker")

    heals = apply_battle_heal([attacker], rng)

    assert len(heals) == 1
    assert heals[0]["healed"] == 100  # 1000 * 10%
    assert attacker.hp == 1100
    assert attacker.troop_strength == 60
    assert heals[0]["unit_state"]["current"] == 60
    assert heals[0]["unit_state"]["maximum"] == 100
    assert heals[0]["unit_state"]["percent"] == 60
