from __future__ import annotations

from types import SimpleNamespace

import pytest

from battle.models import TroopTemplate
from gameplay.models import PlayerTroop
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl import finalization_helpers
from gameplay.services.missions_impl.finalization_helpers import (
    build_mission_drops_with_salvage,
    extract_report_guest_state,
    return_attacker_troops_after_mission,
)
from guests.models import Guest, GuestStatus, GuestTemplate
from guests.query_utils import guest_template_rarity_rank_case


def test_extract_report_guest_state_rejects_invalid_hp_update_payload():
    report = SimpleNamespace(
        losses={"attacker": {"hp_updates": {"guest-1": "bad"}}},
        attacker_team=[],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report hp update payload"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_invalid_team_entry():
    report = SimpleNamespace(
        losses={},
        attacker_team=[{"guest_id": "x", "remaining_hp": 10}],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report team entry"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_negative_hp_update():
    report = SimpleNamespace(
        losses={"attacker": {"hp_updates": {1: -1}}},
        attacker_team=[],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report hp update payload"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_non_positive_hp_update_guest_id():
    report = SimpleNamespace(
        losses={"attacker": {"hp_updates": {0: 10}}},
        attacker_team=[],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report hp update payload"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_invalid_losses_container():
    report = SimpleNamespace(
        losses="bad-losses",
        attacker_team=[],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report.losses"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_missing_losses_container():
    report = SimpleNamespace(
        losses=None,
        attacker_team=[],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report.losses"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_invalid_team_entries_container():
    report = SimpleNamespace(
        losses={},
        attacker_team="bad-team",
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report.team_entries"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_missing_team_entries_container():
    report = SimpleNamespace(
        losses={},
        attacker_team=None,
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report.team_entries"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_non_mapping_team_entry():
    report = SimpleNamespace(
        losses={},
        attacker_team=["bad-entry"],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report team entry"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_negative_team_entry_hp():
    report = SimpleNamespace(
        losses={},
        attacker_team=[{"guest_id": 1, "remaining_hp": -1}],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report team entry"):
        extract_report_guest_state(report, "attacker")


def test_extract_report_guest_state_rejects_non_positive_team_entry_guest_id():
    report = SimpleNamespace(
        losses={},
        attacker_team=[{"guest_id": 0, "remaining_hp": 10}],
        defender_team=[],
    )

    with pytest.raises(AssertionError, match="invalid mission report team entry"):
        extract_report_guest_state(report, "attacker")


def test_return_attacker_troops_after_mission_rejects_invalid_troop_loadout():
    locked_run = SimpleNamespace(
        mission=SimpleNamespace(is_defense=False),
        troop_loadout="bad-loadout",
        is_retreating=False,
        manor=SimpleNamespace(id=1),
        id=11,
    )

    with pytest.raises(AssertionError, match="invalid mission troop_loadout"):
        return_attacker_troops_after_mission(
            locked_run, report=None, logger=SimpleNamespace(warning=lambda *_a, **_k: None)
        )


def test_build_mission_drops_with_salvage_rejects_invalid_report_drops():
    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={}), id=12)
    report = SimpleNamespace(drops="bad-drops")

    with pytest.raises(AssertionError, match="invalid mission report.drops"):
        build_mission_drops_with_salvage(
            locked_run,
            report,
            "attacker",
            logger=SimpleNamespace(),
            resolve_defense_drops_if_missing=lambda *_a, **_k: {},
        )


def test_build_mission_drops_with_salvage_rejects_missing_report_drops():
    locked_run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={}), id=13)
    report = SimpleNamespace(drops=None)

    with pytest.raises(AssertionError, match="invalid mission report.drops"):
        build_mission_drops_with_salvage(
            locked_run,
            report,
            "attacker",
            logger=SimpleNamespace(),
            resolve_defense_drops_if_missing=lambda *_a, **_k: {},
        )


@pytest.mark.django_db
def test_build_defense_report_if_needed_uses_all_idle_defenders_only(django_user_model):
    user = django_user_model.objects.create_user(username="mission_defense_idle_only", password="pass12345")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="mission_defense_idle_only_tpl",
        name="防守任务门客",
        archetype="military",
        rarity="green",
        base_attack=120,
        base_intellect=90,
        base_defense=100,
        base_agility=90,
        base_luck=50,
        base_hp=1500,
    )
    idle_guest_1 = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.IDLE,
        level=20,
        force=200,
        intellect=90,
        defense_stat=110,
        agility=95,
        current_hp=1200,
    )
    idle_guest_2 = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.IDLE,
        level=18,
        force=180,
        intellect=88,
        defense_stat=108,
        agility=92,
        current_hp=1180,
    )
    Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.WORKING,
        level=22,
        force=210,
        intellect=91,
        defense_stat=109,
        agility=93,
        current_hp=1190,
    )
    Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.INJURED,
        level=16,
        force=150,
        intellect=80,
        defense_stat=90,
        agility=85,
        current_hp=700,
    )
    troop_template = TroopTemplate.objects.create(key="mission_defense_idle_only_guard", name="守方护院")
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=6)

    mission = SimpleNamespace(is_defense=True, name="防守任务")
    locked_run = SimpleNamespace(
        battle_report=None,
        is_retreating=False,
        mission=mission,
        manor=manor,
        id=99,
        save=lambda **_kwargs: None,
    )
    captured: dict[str, object] = {}

    def _fake_generate_sync_battle_report(**kwargs):
        captured["guest_ids"] = [guest.id for guest in kwargs["guests"]]
        captured["loadout"] = kwargs["loadout"]
        return SimpleNamespace(id=99)

    report = finalization_helpers.build_defense_report_if_needed(
        locked_run,
        guest_template_rarity_rank_case=guest_template_rarity_rank_case,
        generate_sync_battle_report=_fake_generate_sync_battle_report,
    )

    assert report.id == 99
    assert captured["guest_ids"] == [idle_guest_1.id, idle_guest_2.id]
    assert captured["loadout"] == {"mission_defense_idle_only_guard": 6}
