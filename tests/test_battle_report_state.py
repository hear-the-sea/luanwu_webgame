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


def test_snapshot_round_lineups_keeps_only_remaining_guests_and_troops():
    from battle.simulation.report_state import snapshot_round_lineups

    attacker_guest = make_unit(
        name="赵云",
        side="attacker",
        template_key="zhao_yun",
        guest_id=11,
        hp=680,
        max_hp=1000,
    )
    fallen_guest = make_unit(
        name="关羽",
        side="attacker",
        template_key="guan_yu",
        guest_id=12,
        hp=0,
        max_hp=1200,
    )
    attacker_troop = make_unit(
        name="刀圣",
        side="attacker",
        kind="troop",
        template_key="dao_sheng",
        hp=5000,
        max_hp=8000,
        troop_strength=500,
        initial_troop_strength=800,
    )
    depleted_troop = make_unit(
        name="剑圣",
        side="attacker",
        kind="troop",
        template_key="jian_sheng",
        hp=1,
        max_hp=8000,
        troop_strength=0,
        initial_troop_strength=800,
    )
    city_defense = make_unit(
        name="箭塔",
        side="defender",
        kind="city_defense",
        template_key="arrow_tower",
        level=4,
        hp=5000,
        max_hp=6000,
    )

    lineups = snapshot_round_lineups(
        [attacker_guest, fallen_guest, attacker_troop, depleted_troop],
        [city_defense],
    )

    assert lineups == {
        "attacker": {
            "guests": [
                {
                    "name": "赵云",
                    "template_key": "zhao_yun",
                    "guest_id": 11,
                    "current_hp": 680,
                    "max_hp": 1000,
                }
            ],
            "city_defenses": [],
            "troops": [{"name": "刀圣", "template_key": "dao_sheng", "count": 500}],
        },
        "defender": {
            "guests": [],
            "city_defenses": [
                {
                    "name": "箭塔",
                    "template_key": "arrow_tower",
                    "level": 4,
                    "hp": 5000,
                    "max_hp": 6000,
                }
            ],
            "troops": [],
        },
    }
