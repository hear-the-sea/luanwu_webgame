from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot, load_entry_guests
from gameplay.services.arena.virtual_lineups import snapshot_power
from gameplay.services.arena.virtual_reserve_references import reference_snapshots


def _make_mock_guest():
    guest = MagicMock()
    guest.display_name = "竞技门客"
    guest.rarity = "blue"
    guest.template.key = "arena_guest_tpl"
    guest.level = 12
    guest.force = 88
    guest.intellect = 77
    guest.defense_stat = 66
    guest.agility = 55
    guest.luck = 44
    guest.max_hp = 1000
    guest.current_hp = 1200
    guest.stat_block.return_value = {"attack": 123, "defense": 98, "hp": 900}
    guest.skills.values_list.return_value = [" skill_a ", "", "skill_b"]
    return guest


def test_build_entry_guest_snapshot_clamps_hp_and_serializes_skill_keys():
    guest = _make_mock_guest()

    snapshot = build_entry_guest_snapshot(guest)

    assert snapshot == {
        "snapshot_version": 1,
        "display_name": "竞技门客",
        "rarity": "blue",
        "template_key": "arena_guest_tpl",
        "level": 12,
        "force": 88,
        "intellect": 77,
        "defense_stat": 66,
        "agility": 55,
        "luck": 44,
        "attack": 123,
        "defense": 98,
        "max_hp": 900,
        "current_hp": 900,
        "skill_keys": ["skill_a", "skill_b"],
    }


def test_snapshot_power_includes_guest_agility():
    base = {"attack": 123, "defense": 98, "max_hp": 900, "agility": 0}

    assert snapshot_power({**base, "agility": 55}) - snapshot_power(base) == 55


def test_reference_snapshots_mark_legacy_missing_agility_without_mutating_input():
    source = {"attack": 123, "defense": 98, "max_hp": 900, "current_hp": 900}

    class _Links:
        def all(self):
            return [SimpleNamespace(snapshot=source, guest=None)]

    snapshots = reference_snapshots(SimpleNamespace(entry_guests=_Links()))

    assert source == {"attack": 123, "defense": 98, "max_hp": 900, "current_hp": 900}
    assert snapshots == [
        {
            "attack": 123,
            "defense": 98,
            "max_hp": 900,
            "current_hp": 900,
            "arena_power_snapshot_semantics": "legacy_missing_agility",
        }
    ]


def test_arena_guest_snapshot_proxy_exposes_expected_fields():
    proxy = ArenaGuestSnapshotProxy(
        {
            "display_name": "快照门客",
            "rarity": "purple",
            "template_key": "proxy_tpl",
            "level": 20,
            "force": 100,
            "intellect": 90,
            "defense_stat": 80,
            "agility": 70,
            "luck": 60,
            "attack": 150,
            "defense": 120,
            "max_hp": 1000,
            "current_hp": 800,
            "skill_keys": [" skill_x ", "", "skill_y"],
        }
    )

    assert proxy.display_name == "快照门客"
    assert proxy.rarity == "purple"
    assert proxy.template.key == "proxy_tpl"
    assert proxy.attack == 150
    assert proxy.defense == 120
    assert proxy.max_hp == 1000
    assert proxy._override_skills == ["skill_x", "skill_y"]


def test_load_entry_guests_uses_snapshot_and_falls_back_to_live_guest_snapshot():
    guest = _make_mock_guest()
    live_only_link = SimpleNamespace(snapshot={}, guest=guest)
    snapshot_link = SimpleNamespace(
        snapshot={
            "display_name": "已有快照",
            "rarity": "green",
            "template_key": "snap_tpl",
            "level": 5,
            "force": 10,
            "intellect": 11,
            "defense_stat": 12,
            "agility": 13,
            "luck": 14,
            "attack": 15,
            "defense": 16,
            "max_hp": 17,
            "current_hp": 18,
            "skill_keys": ["skill_z"],
        },
        guest=None,
    )

    class _FakeOrderedLinks(list):
        def order_by(self, *_args, **_kwargs):
            return self

    entry = SimpleNamespace(entry_guests=_FakeOrderedLinks([live_only_link, snapshot_link]))

    proxies = load_entry_guests(entry, max_guests_per_entry=10)

    assert [proxy.display_name for proxy in proxies] == ["竞技门客", "已有快照"]
    assert proxies[0]._override_skills == ["skill_a", "skill_b"]
    assert proxies[1].template.key == "snap_tpl"
