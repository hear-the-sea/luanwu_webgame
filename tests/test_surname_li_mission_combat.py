"""姓氏传说之李的正式敌将构造与战斗流程。"""

from importlib import import_module
from random import Random
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from battle.combatants_pkg import (
    assign_agility_based_priorities,
    build_guest_combatants,
    build_named_ai_guests,
    build_troop_combatants,
    clear_guest_template_cache,
)
from battle.simulation.battle_flow import simulate_battle
from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels
from gameplay.models import MissionTemplate

rules = import_module("gameplay.migrations.0237_surname_li_mission")


@pytest.mark.django_db
def test_elai_mission_builds_real_troops_and_executes_formal_battle(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    rules.sync_surname_li_mission(apps, SimpleNamespace(connection=connection))
    clear_guest_template_cache()
    mission = MissionTemplate.objects.get(key="surname_legend_li")
    tech = mission.enemy_technology
    enemies = build_named_ai_guests(mission.enemy_guests, level=tech["guest_level"], rng=Random(13))
    defenders = build_guest_combatants(enemies, "defender", stat_bonuses=get_guest_stat_bonuses(tech))
    assert len(defenders) == 1
    assert defenders[0].template_key == "task_surname_li_elai"
    assert {skill["key"] for skill in defenders[0].skills} == set(rules.GUEST_SKILLS["task_surname_li_elai"])
    defenders += build_troop_combatants(mission.enemy_troops, "defender", tech_levels=resolve_enemy_tech_levels(tech))
    assert sum(unit.initial_troop_strength for unit in defenders) == 1000
    players = build_named_ai_guests(
        [{"key": "hero_li_lizhen"}, {"key": "hero_heishan_laoyao"}], level=100, rng=Random(23)
    )
    attackers = build_guest_combatants(players, "attacker")
    assign_agility_based_priorities(attackers, defenders)
    result = simulate_battle(attackers, defenders, Random(31), seed=31, travel_seconds=0, config={}, max_rounds=8)
    assert result.rounds
    assert all(unit.hp >= 0 for unit in attackers + defenders)
