from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from battle.execution import BattleOptions, prepare_snapshot_battle_teams
from battle.models import BattleReport
from battle.services import simulate_report
from gameplay.models import ArenaRingAction, ArenaRingDaily, ArenaRingMatch, ArenaRingTenure
from gameplay.services.arena.ring import challenge_ring, occupy_ring, update_ring_lineup
from gameplay.services.arena.ring_snapshots import ArenaRingError, build_ring_lineup, ring_guest_proxies
from tests.test_arena_ring import occupy
from tests.test_arena_ring import ring_players as _ring_players_fixture
from tests.test_arena_ring import target

pytestmark = pytest.mark.django_db(transaction=True)
ring_players = _ring_players_fixture


def _report(manor, winner="defender"):
    return BattleReport(
        manor=manor, opponent_name="擂主", winner=winner, starts_at=timezone.now(), completed_at=timezone.now()
    )


def test_ring_simulation_releases_transaction_and_defers_report_write(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)

    def simulate(**kwargs):
        assert not connection.in_atomic_block
        assert kwargs["persist_report"] is False
        assert not BattleReport.objects.exists()
        assert not ArenaRingDaily.objects.filter(manor=b).exists()
        return _report(b)

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    result = challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert BattleReport.objects.count() == ArenaRingMatch.objects.count() == 1
    assert ArenaRingDaily.objects.get(manor=b).challenges == 1
    assert result["battle_report_id"] == BattleReport.objects.get().pk


@pytest.mark.parametrize("change", ["lineup", "expiry", "quota", "other_ring"])
def test_ring_commit_rechecks_changes_during_simulation(ring_players, monkeypatch, change):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)

    def simulate(**kwargs):
        if change == "lineup":
            update_ring_lineup(**target(a, occupied), guest_ids=[ga.pk], troop_loadout={})
        elif change == "expiry":
            deadline = ArenaRingTenure.objects.get(pk=occupied["tenure_id"]).ends_at
            monkeypatch.setattr("gameplay.services.arena.ring.timezone.now", lambda: deadline + timedelta(seconds=1))
        elif change == "quota":
            ArenaRingDaily.objects.create(manor=b, date=timezone.localdate(), challenges=10)
        else:
            occupy_ring(
                manor=b,
                ring_key="baihu",
                action_token=uuid4(),
                expected_version=0,
                guest_ids=[gb.pk],
                troop_loadout={},
            )
        return _report(b)

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    with pytest.raises(ArenaRingError):
        challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert not BattleReport.objects.exists()
    assert not ArenaRingMatch.objects.exists()
    assert not ArenaRingAction.objects.filter(kind="challenge").exists()


@pytest.mark.parametrize("same_token", [True, False])
def test_competing_completion_is_idempotent_or_rechecks_cooldown(ring_players, monkeypatch, same_token):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    kwargs = dict(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    competing = dict(kwargs)
    if not same_token:
        competing["action_token"] = uuid4()
    results = []
    nested = False

    def simulate(**_kwargs):
        nonlocal nested
        if not nested:
            nested = True
            results.append(challenge_ring(**competing))
        return _report(b)

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    if same_token:
        assert challenge_ring(**kwargs) == results[0]
    else:
        with pytest.raises(ArenaRingError, match="冷却"):
            challenge_ring(**kwargs)
    assert BattleReport.objects.count() == ArenaRingMatch.objects.count() == 1
    assert ArenaRingDaily.objects.get(manor=b).challenges == 1


def test_failed_receipt_commit_rolls_back_report_match_and_quota(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", lambda **_kwargs: _report(b))

    def fail(**_kwargs):
        raise IntegrityError("receipt storage failed")

    monkeypatch.setattr(ArenaRingAction.objects, "create", fail)
    with pytest.raises(IntegrityError, match="receipt storage"):
        challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert not BattleReport.objects.exists()
    assert not ArenaRingMatch.objects.exists()
    assert not ArenaRingDaily.objects.filter(manor=b).exists()


def test_unsaved_snapshot_battle_does_not_write_or_apply_live_side_effects(ring_players, monkeypatch):
    from django.db import transaction

    import battle.execution as execution

    a, b, ga, gb = ring_players
    with transaction.atomic():
        attacker = build_ring_lineup(b, [gb.pk], {})
        defender = build_ring_lineup(a, [ga.pk], {})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("snapshot simulation attempted a live side effect")

    simulate = execution.simulate_battle

    def checked_simulation(*args, **kwargs):
        assert not connection.in_atomic_block
        return simulate(*args, **kwargs)

    monkeypatch.setattr(execution, "simulate_battle", checked_simulation)
    monkeypatch.setattr("battle.execution._recover_guest_hp_batch", forbidden)
    monkeypatch.setattr("battle.execution.grant_battle_victory_loyalty", forbidden)
    monkeypatch.setattr("battle.execution.dispatch_battle_message", forbidden)
    report = simulate_report(
        manor=b,
        attacker_guests=ring_guest_proxies(attacker),
        defender_guests=ring_guest_proxies(defender),
        troop_loadout={},
        fill_default_troops=False,
        battle_type="arena",
        auto_reward=False,
        apply_damage=False,
        send_message=False,
        use_lock=False,
        persist_report=False,
        attacker_tech_levels=attacker["technology"]["levels"],
        defender_setup={"technology": defender["technology"], "troop_loadout": {}},
    )
    assert report.pk is None
    assert report.rounds
    assert not BattleReport.objects.exists()
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.arena_coins == b.arena_coins == 0


@pytest.mark.parametrize(
    "unsafe",
    [
        {"auto_reward": True},
        {"apply_damage": True},
        {"send_message": True},
        {"use_lock": True},
        {"drop_handler": lambda _drops: None},
        {"defender_manor": object()},
        {"attacker_guests": []},
        {"defender_guests": []},
    ],
)
def test_unsaved_battle_rejects_side_effects_before_work(unsafe):
    from types import SimpleNamespace

    proxy = SimpleNamespace(is_battle_snapshot_proxy=True)
    kwargs = dict(
        manor=None,
        attacker_guests=[proxy],
        defender_guests=[proxy],
        auto_reward=False,
        apply_damage=False,
        send_message=False,
        use_lock=False,
        persist_report=False,
    )
    kwargs.update(unsafe)
    with pytest.raises(ValueError, match="side-effect-free"):
        simulate_report(**kwargs)


def test_unsaved_battle_rejects_real_guest_even_if_marked_as_proxy(ring_players):
    _a, b, ga, gb = ring_players
    ga.is_battle_snapshot_proxy = gb.is_battle_snapshot_proxy = True
    with pytest.raises(ValueError, match="side-effect-free"):
        simulate_report(
            manor=b,
            attacker_guests=[gb],
            defender_guests=[ga],
            auto_reward=False,
            apply_damage=False,
            send_message=False,
            use_lock=False,
            persist_report=False,
        )


def test_prepared_snapshot_uses_frozen_skills_troops_and_rules_without_queries(ring_players, monkeypatch):
    from django.core.cache import cache
    from django.core.cache.backends.locmem import LocMemCache
    from django.db import transaction

    import battle.execution as execution
    from battle.models import TroopTemplate
    from battle.troops import invalidate_troop_templates_cache
    from guests.models import Skill

    a, b, ga, gb = ring_players
    monkeypatch.setattr("battle.troops.cache", LocMemCache(f"ring-frozen-{uuid4()}", {}))
    skill = Skill.objects.create(key="frozen_ring_skill", name="固定技能", base_power=42)
    gb.skills.add(skill)
    troop = TroopTemplate.objects.create(key="ring_frozen_troop", name="固定护院", base_attack=31)
    invalidate_troop_templates_cache()
    with transaction.atomic():
        attacker = build_ring_lineup(b, [gb.pk], {troop.key: 1})
        defender = build_ring_lineup(a, [ga.pk], {})
        attacker_proxies = ring_guest_proxies(attacker)
        defender_proxies = ring_guest_proxies(defender)
        options = BattleOptions(
            seed=17,
            battle_type="arena",
            troop_loadout=attacker["troops"],
            defender_guests=defender_proxies,
            defender_setup={"technology": defender["technology"], "troop_loadout": {}},
            attacker_tech_levels=attacker["technology"]["levels"],
            attacker_manor=None,
            fill_default_troops=False,
            auto_reward=False,
            apply_damage=False,
            apply_victory_loyalty=False,
            recover_live_guest_hp=False,
            send_message=False,
            persist_report=False,
        )
        prepared = prepare_snapshot_battle_teams(b, attacker_proxies, options)
    assert prepared.attacker_guests[0].skills[0]["power"] == 42
    original_hp = prepared.attacker_guests[0].hp
    original_troop_attack = prepared.attacker_troops[0].attack
    Skill.objects.filter(pk=skill.pk).update(base_power=999)
    TroopTemplate.objects.filter(pk=troop.pk).update(base_attack=999)
    options.troop_loadout.clear()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("prepared simulation attempted database, cache or catalog access")

    monkeypatch.setattr(execution, "get_battle_config", forbidden)
    monkeypatch.setattr(execution, "_prepare_battle_environment", forbidden)
    monkeypatch.setattr(execution, "_build_attacker_units", forbidden)
    monkeypatch.setattr(execution, "_build_defender_units", forbidden)
    monkeypatch.setattr(cache, "get", forbidden)
    monkeypatch.setattr(cache, "set", forbidden)
    with connection.execute_wrapper(forbidden):
        reports = [
            simulate_report(
                manor=b,
                attacker_guests=attacker_proxies,
                defender_guests=defender_proxies,
                auto_reward=False,
                apply_damage=False,
                send_message=False,
                use_lock=False,
                persist_report=False,
                prepared_teams=prepared,
            )
            for _ in range(2)
        ]
    assert reports[0].rounds == reports[1].rounds
    assert reports[0].losses == reports[1].losses
    assert reports[0].attacker_troops[troop.key] == 1
    assert prepared.attacker_guests[0].skills[0]["power"] == 42
    assert prepared.attacker_guests[0].hp == original_hp
    assert prepared.attacker_troops[0].attack == original_troop_attack
    assert all(report.pk is None for report in reports)
    assert not BattleReport.objects.exists()


def test_ring_prepares_skill_and_troop_definitions_before_releasing_transaction(ring_players, monkeypatch):
    import battle.execution as execution

    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    original = execution._build_attacker_units
    preparations = []

    def prepare(*args, **kwargs):
        assert connection.in_atomic_block
        preparations.append(True)
        return original(*args, **kwargs)

    def simulate(**kwargs):
        assert not connection.in_atomic_block
        assert kwargs["prepared_teams"].manor_id == b.pk
        return _report(b)

    monkeypatch.setattr(execution, "_build_attacker_units", prepare)
    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert preparations == [True]
