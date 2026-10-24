import threading

import pytest
from django.db import connection, connections
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import ArenaRingDaily, ArenaRingInspection, ArenaRingMatch
from gameplay.services.arena.ring import challenge_ring, inspect_ring, update_ring_lineup
from gameplay.services.arena.ring_snapshots import ArenaRingError
from tests.test_arena_ring import occupy
from tests.test_arena_ring import ring_players as _ring_players_fixture
from tests.test_arena_ring import target

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
ring_players = _ring_players_fixture


def _guard_test_database():
    assert connection.vendor == "mysql"
    assert str(connection.settings_dict["NAME"]).startswith("test_"), "isolated test database required"


@pytest.fixture(autouse=True)
def _require_isolated_mysql():
    if connection.vendor != "mysql":
        pytest.skip("ring concurrency requires MySQL row locks")
    _guard_test_database()


def _worker(operation, results, errors):
    connections.close_all()
    try:
        _guard_test_database()
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
        results.append(operation())
    except Exception as exc:  # pragma: no cover - asserted by caller
        errors.append(exc)
    finally:
        connections.close_all()


def _report(manor):
    return BattleReport(
        manor=manor, opponent_name="擂主", winner="defender", starts_at=timezone.now(), completed_at=timezone.now()
    )


def test_concurrent_same_token_simulations_commit_one_report(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    kwargs = dict(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    simulations = threading.Barrier(2)
    results, errors = [], []

    def simulate(**_kwargs):
        assert not connection.in_atomic_block
        # 两个准备事务必须已释放全局锁，才能一起到达此处。
        simulations.wait(timeout=8)
        return _report(b)

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    workers = [
        threading.Thread(target=_worker, args=(lambda: challenge_ring(**kwargs), results, errors), daemon=True)
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    assert len(results) == 2 and results[0] == results[1]
    assert BattleReport.objects.count() == ArenaRingMatch.objects.count() == 1
    assert ArenaRingDaily.objects.get(manor=b).challenges == 1


def test_lineup_update_commits_while_simulation_is_paused_and_rejects_stale_result(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    entered, release, updated = threading.Event(), threading.Event(), threading.Event()
    results, errors, update_results, update_errors = [], [], [], []

    def simulate(**_kwargs):
        entered.set()
        assert release.wait(timeout=15), "paused simulation was not released"
        return _report(b)

    def update():
        result = update_ring_lineup(**target(a, occupied), guest_ids=[ga.pk], troop_loadout={})
        updated.set()
        return result

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    challenge = threading.Thread(
        target=_worker,
        args=(lambda: challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={}), results, errors),
        daemon=True,
    )
    updater = threading.Thread(target=_worker, args=(update, update_results, update_errors), daemon=True)
    try:
        challenge.start()
        assert entered.wait(timeout=8), "challenge did not reach simulation"
        updater.start()
        assert updated.wait(timeout=6), "lineup update waited for simulation's global locks"
    finally:
        release.set()
        challenge.join(timeout=15)
        if updater.ident is not None:
            updater.join(timeout=15)
    assert not challenge.is_alive() and not updater.is_alive()
    assert update_errors == [] and len(update_results) == 1
    assert len(errors) == 1 and isinstance(errors[0], ArenaRingError)
    assert not BattleReport.objects.exists()
    assert not ArenaRingMatch.objects.exists()
    assert not ArenaRingDaily.objects.filter(manor=b).exists()


def test_concurrent_inspections_share_one_version_snapshot(ring_players):
    a, b, ga, _gb = ring_players
    occupied = occupy(a, ga)
    start = threading.Barrier(2)
    results, errors = [], []

    def inspect():
        start.wait(timeout=8)
        return inspect_ring(**target(b, occupied))

    workers = [threading.Thread(target=_worker, args=(inspect, results, errors), daemon=True) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    assert len(results) == 2 and results[0]["inspection_id"] == results[1]["inspection_id"]
    assert ArenaRingInspection.objects.count() == 1
