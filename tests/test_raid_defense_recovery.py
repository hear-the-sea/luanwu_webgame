from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from battle.models import BattleReport
from core.config import BUILDING_KEYS
from gameplay.constants import PVPConstants
from gameplay.models import RaidRun
from gameplay.services.city_defense_rules import city_defense_max_hp
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.raid.combat.defense_recovery import has_recent_defensive_battle
from guests.models import Guest, GuestStatus, GuestTemplate
from tests.raid_combat_battle.support import build_attacker_defender


def _create_report(manor, *, now, winner="attacker"):
    return BattleReport.objects.create(
        manor=manor,
        opponent_name="防守方",
        winner=winner,
        starts_at=now,
        completed_at=now,
    )


@pytest.mark.django_db
def test_repeat_defense_uses_six_hour_sliding_window(django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="repeat_defense_window_a",
        defender_username="repeat_defense_window_d",
    )
    now = timezone.now()
    current_run = RaidRun.objects.create(attacker=attacker, defender=defender)
    prior_report = _create_report(attacker, now=now, winner="defender")
    RaidRun.objects.create(attacker=attacker, defender=defender, battle_report=prior_report)
    window = timedelta(seconds=PVPConstants.RAID_REPEAT_DEFENSE_WINDOW_SECONDS)

    BattleReport.objects.filter(pk=prior_report.pk).update(created_at=now - window - timedelta(seconds=1))
    assert has_recent_defensive_battle(current_run, now=now) is False

    BattleReport.objects.filter(pk=prior_report.pk).update(created_at=now - window + timedelta(seconds=1))
    assert has_recent_defensive_battle(current_run, now=now) is True

    BattleReport.objects.filter(pk=prior_report.pk).update(created_at=now + timedelta(seconds=1))
    assert has_recent_defensive_battle(current_run, now=now) is False


@pytest.mark.django_db
def test_second_defensive_battle_restores_guest_and_city_defense_before_simulation(
    monkeypatch,
    django_user_model,
):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="repeat_defense_heal_a",
        defender_username="repeat_defense_heal_d",
    )
    now = timezone.now()
    prior_report = _create_report(attacker, now=now - timedelta(minutes=1), winner="defender")
    RaidRun.objects.create(attacker=attacker, defender=defender, battle_report=prior_report)
    run = RaidRun.objects.create(attacker=attacker, defender=defender, troop_loadout={})

    template = GuestTemplate.objects.create(
        key="repeat_defense_heal_guest",
        name="防守门客",
        archetype="military",
        rarity="green",
        base_hp=1200,
    )
    guest = Guest.objects.create(manor=defender, template=template, status=GuestStatus.IDLE, current_hp=100)
    wall = defender.buildings.select_related("building_type").get(building_type__key=BUILDING_KEYS.WALL)
    wall.level = 5
    wall.current_hp = 100
    wall.hp_updated_at = now
    wall.save(update_fields=["level", "current_hp", "hp_updated_at"])

    observed = {}

    def _fake_simulate_report(**kwargs):
        observed["guest_hp"] = kwargs["defender_guests"][0].current_hp
        observed["wall_hp"] = type(wall).objects.get(pk=wall.pk).current_hp
        return SimpleNamespace(winner="defender", defender_city_defenses=[])

    monkeypatch.setattr("battle.services.simulate_report", _fake_simulate_report)
    monkeypatch.setattr(combat_battle, "_apply_guest_damage_from_report", lambda *_args, **_kwargs: None)

    combat_battle._execute_raid_battle(run)

    assert run.battle_rewards["defense_recovery"] is True
    assert observed["guest_hp"] == guest.max_hp
    assert observed["wall_hp"] == city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    guest.refresh_from_db()
    wall.refresh_from_db()
    assert guest.current_hp == guest.max_hp
    assert wall.current_hp == observed["wall_hp"]
