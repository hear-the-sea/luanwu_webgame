from tests.battle_passives.support import make_unit


def test_snapshot_guest_uses_max_hp_as_capacity():
    from battle.simulation.report_state import snapshot_unit_state

    unit = make_unit(name="赵云", kind="guest", side="attacker", hp=250, max_hp=1000)

    assert snapshot_unit_state(unit) == {
        "kind": "guest",
        "side": "attacker",
        "current": 250,
        "maximum": 1000,
        "percent": 25,
        "status": "danger",
        "status_label": "状态濒危",
    }


def test_snapshot_troop_uses_initial_strength_as_capacity():
    from battle.simulation.report_state import snapshot_unit_state

    unit = make_unit(
        name="铁甲枪王",
        kind="troop",
        side="defender",
        hp=500,
        max_hp=1000,
        troop_strength=30,
        initial_troop_strength=120,
    )

    state = snapshot_unit_state(unit)

    assert state["current"] == 30
    assert state["maximum"] == 120
    assert state["percent"] == 25


def test_snapshot_clamps_depleted_state_to_zero():
    from battle.simulation.report_state import snapshot_unit_state

    unit = make_unit(name="武痴", kind="guest", side="attacker", hp=-20, max_hp=1000)

    state = snapshot_unit_state(unit)

    assert state["current"] == 0
    assert state["percent"] == 0
    assert state["status"] == "empty"
    assert state["status_label"] == "状态耗尽"


def test_snapshot_clamps_recovered_state_to_capacity():
    from battle.simulation.report_state import snapshot_unit_state

    unit = make_unit(name="赵云", kind="guest", side="attacker", hp=1200, max_hp=1000)

    state = snapshot_unit_state(unit)

    assert state["current"] == 1000
    assert state["percent"] == 100
