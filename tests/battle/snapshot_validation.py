from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from battle.execution import validate_troop_capacity
from core.exceptions import BattlePreparationError, InvalidBattleSnapshotError
from gameplay.services.battle_snapshots import (
    build_guest_battle_snapshot,
    build_guest_snapshot_proxies,
    validate_battle_troop_loadout,
)
from tests.battle.support import build_snapshot_payload


def test_build_guest_snapshot_proxies_rejects_empty_snapshot_payload():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([{}], include_guest_identity=True)
    assert exc_info.value.field_name == "guest_snapshots"


def test_build_guest_snapshot_proxies_rejects_non_mapping_snapshot_payload():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies(["bad-snapshot"], include_guest_identity=True)
    assert exc_info.value.field_name == "guest_snapshots"


@pytest.mark.parametrize("field_name", ["display_name", "rarity", "status"])
def test_build_guest_snapshot_proxies_rejects_blank_required_text_fields(field_name):
    payload = build_snapshot_payload(**{field_name: "  "})

    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([payload], include_guest_identity=True)
    assert exc_info.value.field_name == field_name


def test_build_guest_snapshot_proxies_rejects_missing_template_key():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies(
            [
                {
                    "guest_id": 1,
                    "display_name": "坏快照",
                    "rarity": "green",
                    "level": 1,
                    "force": 1,
                    "intellect": 1,
                    "defense_stat": 1,
                    "agility": 1,
                    "luck": 1,
                    "attack": 1,
                    "defense": 1,
                    "max_hp": 1,
                    "current_hp": 1,
                }
            ],
            include_guest_identity=True,
        )
    assert exc_info.value.field_name == "template_key"


def test_build_guest_snapshot_proxies_rejects_invalid_skill_keys_payload():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([build_snapshot_payload(skill_keys="bad-skills")], include_guest_identity=True)
    assert exc_info.value.field_name == "skill_keys"


def test_build_guest_snapshot_proxies_rejects_missing_guest_id_when_identity_requested():
    payload = build_snapshot_payload()
    payload.pop("guest_id")

    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([payload], include_guest_identity=True)
    assert exc_info.value.field_name == "guest_id"


def test_build_guest_snapshot_proxies_rejects_invalid_manor_id_when_present():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([build_snapshot_payload(manor_id=0)], include_guest_identity=True)
    assert exc_info.value.field_name == "manor_id"


def test_build_guest_snapshot_proxies_rejects_invalid_level():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([build_snapshot_payload(level=0)], include_guest_identity=True)
    assert exc_info.value.field_name == "level"


def test_build_guest_snapshot_proxies_rejects_invalid_current_hp():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([build_snapshot_payload(current_hp=0)], include_guest_identity=True)
    assert exc_info.value.field_name == "current_hp"


def test_build_guest_snapshot_proxies_rejects_negative_troop_capacity():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies([build_snapshot_payload(troop_capacity=-1)], include_guest_identity=True)
    assert exc_info.value.field_name == "troop_capacity"


def test_build_guest_snapshot_proxies_accepts_legacy_payload_without_device_bonuses():
    proxy = build_guest_snapshot_proxies([build_snapshot_payload()], include_guest_identity=True)[0]

    assert proxy.troop_device_bonuses == {}


@pytest.mark.parametrize(
    "troop_device_bonuses",
    [
        "bad-bonuses",
        {"bad_class": {"hp": {"flat": 1, "pct": 0}}},
        {"gong": {"bad_stat": {"flat": 1, "pct": 0}}},
        {"gong": {"hp": {"flat": -1, "pct": 0}}},
        {"gong": {"hp": {"flat": float("inf"), "pct": 0}}},
        {"gong": {"hp": {"flat": float("nan"), "pct": 0}}},
        {"gong": {"hp": {"flat": 10**1000, "pct": 0}}},
    ],
)
def test_build_guest_snapshot_proxies_rejects_invalid_device_bonuses(troop_device_bonuses):
    with pytest.raises(InvalidBattleSnapshotError) as exc_info:
        build_guest_snapshot_proxies(
            [build_snapshot_payload(troop_device_bonuses=troop_device_bonuses)],
            include_guest_identity=True,
        )

    assert exc_info.value.field_name == "troop_device_bonuses"


def test_validate_troop_capacity_uses_snapshot_capacity_without_recomputing_guest_model_rules():
    proxy = build_guest_snapshot_proxies([build_snapshot_payload(troop_capacity=230)], include_guest_identity=True)[0]

    validate_troop_capacity([proxy], {"archer": 230})

    with pytest.raises(BattlePreparationError, match="总带兵上限为230"):
        validate_troop_capacity([proxy], {"archer": 231})


def test_build_guest_snapshot_proxies_rejects_non_list_container():
    with pytest.raises(InvalidBattleSnapshotError, match="门客战斗快照数据无效") as exc_info:
        build_guest_snapshot_proxies({"unexpected": "mapping"}, include_guest_identity=True)
    assert exc_info.value.field_name == "guest_snapshots"


@pytest.mark.parametrize(
    "payload",
    [
        "bad-loadout",
        {"archer": True},
        {"archer": "not-a-number"},
        {"archer": 0},
        {"": 1},
    ],
)
def test_validate_battle_troop_loadout_rejects_corrupt_payload(payload):
    with pytest.raises(InvalidBattleSnapshotError) as exc_info:
        validate_battle_troop_loadout(payload)

    assert exc_info.value.snapshot_kind == "troop_loadout"


def test_build_guest_battle_snapshot_rejects_non_string_skill_values():
    guest = MagicMock()
    guest.display_name = "快照门客"
    guest.rarity = "green"
    guest.status = "idle"
    guest.template.key = "snapshot_tpl"
    guest.level = 1
    guest.force = 1
    guest.intellect = 1
    guest.defense_stat = 1
    guest.agility = 1
    guest.luck = 1
    guest.current_hp = 1
    guest.stat_block.return_value = {"attack": 1, "defense": 1, "hp": 1}
    guest.skills.values_list.return_value = [123]

    with pytest.raises(AssertionError, match="invalid battle guest skill_keys entry"):
        build_guest_battle_snapshot(guest, include_identity=False)


def test_build_guest_battle_snapshot_rejects_non_string_override_skills():
    guest = SimpleNamespace(
        display_name="快照门客",
        rarity="green",
        status="idle",
        template=SimpleNamespace(key="snapshot_tpl"),
        level=1,
        force=1,
        intellect=1,
        defense_stat=1,
        agility=1,
        luck=1,
        current_hp=1,
        attack_bonus=0,
        defense_bonus=0,
        skills=None,
        _override_skills=[123],
        stat_block=lambda: {"attack": 1, "defense": 1, "hp": 1},
    )

    with pytest.raises(AssertionError, match="invalid battle guest override skill_keys entry"):
        build_guest_battle_snapshot(guest, include_identity=False)


def test_build_guest_battle_snapshot_rejects_invalid_template_key():
    guest = MagicMock()
    guest.display_name = "快照门客"
    guest.rarity = "green"
    guest.status = "idle"
    guest.template.key = ""
    guest.level = 1
    guest.force = 1
    guest.intellect = 1
    guest.defense_stat = 1
    guest.agility = 1
    guest.luck = 1
    guest.current_hp = 1
    guest.skills.values_list.return_value = []
    guest.stat_block.return_value = {"attack": 1, "defense": 1, "hp": 1}

    with pytest.raises(AssertionError, match="invalid battle guest template.key"):
        build_guest_battle_snapshot(guest, include_identity=False)


def test_build_guest_battle_snapshot_rejects_invalid_identity_fields():
    guest = MagicMock()
    guest.id = 0
    guest.manor_id = 1
    guest.display_name = "快照门客"
    guest.rarity = "green"
    guest.status = "idle"
    guest.template.key = "snapshot_tpl"
    guest.level = 1
    guest.force = 1
    guest.intellect = 1
    guest.defense_stat = 1
    guest.agility = 1
    guest.luck = 1
    guest.current_hp = 1
    guest.skills.values_list.return_value = []
    guest.stat_block.return_value = {"attack": 1, "defense": 1, "hp": 1}

    with pytest.raises(AssertionError, match="invalid battle guest id"):
        build_guest_battle_snapshot(guest, include_identity=True)


def test_build_guest_battle_snapshot_rejects_blank_display_name(monkeypatch):
    guest = SimpleNamespace(
        display_name=" ",
        rarity="green",
        status="idle",
        template=SimpleNamespace(key="snapshot_tpl"),
        level=1,
        force=1,
        intellect=1,
        defense_stat=1,
        agility=1,
        luck=1,
        current_hp=5,
        skills=SimpleNamespace(values_list=lambda *_a, **_k: []),
    )
    monkeypatch.setattr(
        "gameplay.services.battle_snapshots.resolve_guest_combat_stats",
        lambda _guest: SimpleNamespace(attack=1, defense=1, max_hp=10, troop_capacity=0),
    )

    with pytest.raises(AssertionError, match="invalid battle guest display_name"):
        build_guest_battle_snapshot(guest, include_identity=False)


def test_build_guest_battle_snapshot_clamps_current_hp_exceeding_max_hp(monkeypatch):
    guest = SimpleNamespace(
        display_name="快照门客",
        rarity="green",
        status="idle",
        template=SimpleNamespace(key="snapshot_tpl"),
        level=1,
        force=1,
        intellect=1,
        defense_stat=1,
        agility=1,
        luck=1,
        current_hp=11,
        skills=SimpleNamespace(values_list=lambda *_a, **_k: []),
    )
    monkeypatch.setattr(
        "gameplay.services.battle_snapshots.resolve_guest_combat_stats",
        lambda _guest: SimpleNamespace(attack=1, defense=1, max_hp=10, troop_capacity=0),
    )

    payload = build_guest_battle_snapshot(guest, include_identity=False)

    assert payload["current_hp"] == 10
    assert payload["max_hp"] == 10
