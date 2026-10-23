import random
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from battle.arena_coop import adjust_arena_coop_damage
from battle.passives import run_passives_for_timing
from battle.report_events import iter_damage_events
from battle.simulation.battle_flow import simulate_battle
from battle.simulation.damage_application import apply_damage_results
from battle.status_manager import prepare_combatants_for_round
from battle.view_helpers import build_rounds_display
from tests.test_battle_damage_invariants import _make_guest


class FixedRng:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.value


def qiankun_skill():
    skills = yaml.safe_load(Path("data/arena_coop_special_skills.yaml").read_text())["skills"]
    return deepcopy(next(skill for skill in skills if skill["key"] == "gl_top_qiankun_shift"))


@pytest.mark.parametrize("hp_ratio", [0.2, 1.0])
@pytest.mark.parametrize("roll,success", [(0.0, True), (0.749, True), (0.75, False), (0.99, False)])
def test_qiankun_rolls_once_at_round_start_without_low_hp_requirement(hp_ratio, roll, success):
    actor = _make_guest(name="张无忌", side="defender", hp=10000)
    actor.hp = int(actor.max_hp * hp_ratio)
    actor.skills = [qiankun_skill()]
    rng = FixedRng(roll)
    events = []
    context = dict(
        actor=actor, target=None, attacker_team=[], defender_team=[actor], round_no=1, event_sink=events, rng=rng
    )
    run_passives_for_timing("round_start", **context)
    assert rng.calls == 1
    assert len(events) == int(success)
    assert (actor.battle_modifiers.get("reflect_ratio") == 0.1) == success
    if success:
        assert events[0]["message"] == "本回合获得卸力反震"
        assert actor.battle_modifiers["reflect_cap"] == 8000
    run_passives_for_timing("hit_taken", **context)
    assert rng.calls == 1
    assert len(events) == int(success)
    prepare_combatants_for_round([], [actor], 2)
    assert "reflect_ratio" not in actor.battle_modifiers
    context.update(round_no=2, rng=FixedRng(0.99), event_sink=[])
    run_passives_for_timing("round_start", **context)
    assert "reflect_ratio" not in actor.battle_modifiers
    assert context["event_sink"] == []


def test_round_start_qiankun_notice_stays_in_owners_column():
    event = {
        "type": "passive",
        "unit": "张无忌",
        "side": "defender",
        "effect": "乾坤大挪移",
        "message": "本回合获得卸力反震",
        "unit_state": {"current": 1000},
    }
    attack = {"actor": "段誉", "side": "attacker", "target": "张无忌", "damage": 100}
    rows = build_rounds_display([{"events": [event, attack]}], {})[0]
    assert rows["defender_events"] == [event]
    assert rows["attacker_events"] == [attack]


def test_qiankun_softcap_and_reflection_expire_before_failed_next_round():
    attacker = _make_guest(name="段誉", side="attacker", hp=200000)
    target = _make_guest(name="张无忌", side="defender", hp=200000)
    target.skills = [qiankun_skill()]
    prepare_combatants_for_round([attacker], [target], 1)
    context = dict(
        actor=target,
        target=None,
        attacker_team=[attacker],
        defender_team=[target],
        round_no=1,
        event_sink=[],
        rng=FixedRng(0.1),
    )
    run_passives_for_timing("round_start", **context)
    damage = adjust_arena_coop_damage(attacker, target, 32000)
    assert damage == 21600
    for _ in range(2):
        hit = apply_damage_results(attacker, target, damage, random.Random(1))
        assert hit.reflect_damage == 2160
        assert hit.reflect_effect == "乾坤大挪移"
    prepare_combatants_for_round([attacker], [target], 2)
    context.update(round_no=2, event_sink=[], rng=FixedRng(0.99))
    run_passives_for_timing("round_start", **context)
    assert adjust_arena_coop_damage(attacker, target, 32000) == 32000
    assert "burst_softcap_sources" not in target.battle_modifiers
    assert apply_damage_results(attacker, target, 32000, random.Random(1)).reflect_damage == 0


def test_real_battle_qiankun_only_rolls_before_attacks_and_reflects_in_active_rounds():
    attacker = _make_guest(name="段誉", side="attacker", hp=200000)
    target = _make_guest(name="张无忌", side="defender", hp=200000)
    target.skills = [qiankun_skill()]
    result = simulate_battle([attacker], [target], random.Random(2), 2, 0, {}, drop_table={}, max_rounds=8)
    active_rounds = []
    for battle_round in result.rounds:
        events = battle_round["events"]
        notices = [i for i, event in enumerate(events) if event.get("effect") == "乾坤大挪移"]
        assert len(notices) <= 1
        if notices:
            active_rounds.append(battle_round["round"])
            assert notices[0] < next(i for i, event in enumerate(events) if "damage" in event)
        for hit in iter_damage_events([battle_round]):
            assert not any(event.get("effect") == "乾坤大挪移" for event in hit.get("passive_events_after", []))
            if hit.get("target") == "张无忌" and hit.get("damage", 0) >= 10:
                assert bool(hit.get("reflect_damage")) == bool(notices)
    assert 0 < len(active_rounds) < len(result.rounds)
