from types import SimpleNamespace

import gameplay.services.battle_salvage as battle_salvage_service
import gameplay.services.missions_impl.execution as mission_execution


def test_mission_salvage_passes_player_side_to_equipment_recovery(monkeypatch):
    called = {}

    def _fake_salvage(report, *args, **kwargs):
        called["report"] = report
        called["kwargs"] = kwargs
        return 3, {"equip_duandao": 2}

    monkeypatch.setattr(battle_salvage_service, "calculate_battle_salvage", _fake_salvage)

    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={}), id=99)
    report = SimpleNamespace(drops={"silver": 10}, id=7)

    drops = mission_execution._build_mission_drops_with_salvage(locked_run, report, "defender")

    assert called["report"] is report
    assert called["kwargs"].get("equipment_casualty_side") == "defender"
    assert drops["silver"] == 10
    assert drops["experience_fruit"] == 1
    assert drops["equip_duandao"] == 2


def test_mission_salvage_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        battle_salvage_service,
        "calculate_battle_salvage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken salvage contract")),
    )

    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={}), id=100)
    report = SimpleNamespace(drops={"silver": 10}, id=8)

    with __import__("pytest").raises(AssertionError, match="broken salvage contract"):
        mission_execution._build_mission_drops_with_salvage(locked_run, report, "attacker")


def test_mission_salvage_rejects_invalid_defense_drop_table(monkeypatch):
    monkeypatch.setattr(
        battle_salvage_service,
        "calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )

    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=True, drop_table="bad-drop-table"), id=101)
    report = SimpleNamespace(drops={}, id=9, seed=123)

    with __import__("pytest").raises(AssertionError, match="invalid mission drop table"):
        mission_execution._build_mission_drops_with_salvage(locked_run, report, "defender")


def test_mission_salvage_rejects_invalid_report_drops_container(monkeypatch):
    monkeypatch.setattr(
        battle_salvage_service,
        "calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )

    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={}), id=102)
    report = SimpleNamespace(drops="bad-drops", id=10, seed=123)

    with __import__("pytest").raises(AssertionError, match="invalid mission report.drops"):
        mission_execution._build_mission_drops_with_salvage(locked_run, report, "attacker")


def test_mission_salvage_rejects_missing_report_drops_container(monkeypatch):
    monkeypatch.setattr(
        battle_salvage_service,
        "calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )

    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={}), id=104)
    report = SimpleNamespace(drops=None, id=12, seed=123)

    with __import__("pytest").raises(AssertionError, match="invalid mission report.drops"):
        mission_execution._build_mission_drops_with_salvage(locked_run, report, "attacker")


def test_mission_salvage_rejects_invalid_defense_drop_table_key(monkeypatch):
    monkeypatch.setattr(
        battle_salvage_service,
        "calculate_battle_salvage",
        lambda *_args, **_kwargs: (0, {}),
    )

    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=True, drop_table={"": 1}), id=103)
    report = SimpleNamespace(drops={}, id=11, seed=123)

    with __import__("pytest").raises(AssertionError, match="invalid mission drop table key"):
        mission_execution._build_mission_drops_with_salvage(locked_run, report, "defender")
