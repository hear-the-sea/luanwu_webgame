from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import ArenaRingAction, ArenaRingDaily, ArenaRingInspection, ArenaRingMatch, ArenaRingTenure
from gameplay.selectors.arena.rings import get_ring_event_detail_context
from gameplay.services.arena.ring import inspect_ring, leave_ring, occupy_ring, update_ring_lineup
from gameplay.services.arena.ring_retention import cleanup_ring_history
from gameplay.services.arena.ring_snapshots import ArenaRingError
from tests.test_arena_ring import occupy
from tests.test_arena_ring import ring_players as _ring_players_fixture
from tests.test_arena_ring import target

pytestmark = pytest.mark.django_db
ring_players = _ring_players_fixture


def test_inspection_reuses_same_version_without_mutating_old_snapshot(ring_players):
    a, b, ga, _gb = ring_players
    occupied = occupy(a, ga)
    first = inspect_ring(**target(b, occupied))
    second = inspect_ring(**target(b, occupied))
    assert first["inspection_id"] == second["inspection_id"]
    receipt = ArenaRingInspection.objects.get(pk=first["inspection_id"])
    saved_lineup = receipt.lineup
    ga.level += 10
    ga.save(update_fields=["level"])
    updated = update_ring_lineup(**target(a, occupied), guest_ids=[ga.pk], troop_loadout={})
    third = inspect_ring(**target(b, updated))
    assert third["inspection_id"] != first["inspection_id"]
    receipt.refresh_from_db()
    assert receipt.lineup == saved_lineup
    assert ArenaRingInspection.objects.count() == 2


def test_inspection_reuses_oldest_legacy_duplicate(ring_players):
    a, b, ga, _gb = ring_players
    occupied = occupy(a, ga)
    first = inspect_ring(**target(b, occupied))
    original = ArenaRingInspection.objects.get(pk=first["inspection_id"])
    ArenaRingInspection.objects.create(
        buyer=b, tenure=original.tenure, version=original.version, lineup=original.lineup, created_at=timezone.now()
    )
    assert inspect_ring(**target(b, occupied))["inspection_id"] == original.pk


def test_ring_retention_is_bounded_and_protects_active_receipts(ring_players):
    a, b, ga, gb = ring_players
    now = timezone.now()
    occupied = occupy(a, ga)
    old = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    inspect_kwargs = target(b, occupied)
    inspect_ring(**inspect_kwargs)
    for _ in range(2):
        ArenaRingInspection.objects.create(buyer=b, tenure=old, version=1, lineup=old.lineup, created_at=now)
    for _ in range(2):
        report = BattleReport.objects.create(
            manor=b, opponent_name="擂主", winner="defender", starts_at=now, completed_at=now
        )
        ArenaRingMatch.objects.create(
            tenure=old,
            attacker=b,
            defender=a,
            winner=a,
            battle_report=report,
            pair_low=min(a.pk, b.pk),
            pair_high=max(a.pk, b.pk),
            reward_date=timezone.localdate(),
            created_at=now,
        )
    leave_ring(**target(a, occupied))
    ArenaRingTenure.objects.filter(pk=old.pk).update(ended_at=now - timedelta(days=2))
    active = occupy_ring(
        manor=b, ring_key="baihu", expected_version=0, action_token=uuid4(), guest_ids=[gb.pk], troop_loadout={}
    )
    active_actions = set(
        ArenaRingAction.objects.filter(result__tenure_id=active["tenure_id"]).values_list("pk", flat=True)
    )
    ArenaRingDaily.objects.create(manor=a, date=timezone.localdate(now - timedelta(days=20)))
    first = cleanup_ring_history(now=now, batch_size=1)
    assert first == {"inspections": 1, "actions": 1, "matches": 1, "tenures": 0, "daily": 1}
    assert ArenaRingTenure.objects.filter(pk=old.pk).exists()
    for _ in range(4):
        counts = cleanup_ring_history(now=now, batch_size=1)
        assert all(value <= 1 for value in counts.values())
    assert not ArenaRingTenure.objects.filter(pk=old.pk).exists()
    assert not ArenaRingInspection.objects.filter(tenure_id=old.pk).exists()
    assert not ArenaRingMatch.objects.filter(tenure_id=old.pk).exists()
    assert not ArenaRingAction.objects.filter(result__tenure_id=old.pk).exists()
    assert set(ArenaRingAction.objects.values_list("pk", flat=True)) == active_actions
    assert ArenaRingTenure.objects.filter(pk=active["tenure_id"]).exists()
    assert BattleReport.objects.count() == 2
    with pytest.raises(ArenaRingError):
        inspect_ring(**inspect_kwargs)


@pytest.mark.parametrize("age,deleted", [(timedelta(hours=24), True), (timedelta(hours=24, seconds=-1), False)])
def test_ring_retention_matches_history_boundary(ring_players, age, deleted):
    a, _b, ga, _gb = ring_players
    now = timezone.now()
    occupied = occupy(a, ga)
    leave_ring(**target(a, occupied))
    ArenaRingTenure.objects.filter(pk=occupied["tenure_id"]).update(ended_at=now - age)
    cleanup_ring_history(now=now)
    assert ArenaRingTenure.objects.filter(pk=occupied["tenure_id"]).exists() is not deleted


def test_ring_match_list_does_not_load_battle_payloads(ring_players):
    a, b, ga, _gb = ring_players
    occupied = occupy(a, ga)
    now = timezone.now()
    report = BattleReport.objects.create(
        manor=b, opponent_name="擂主", winner="defender", starts_at=now, completed_at=now, rounds=[{"large": "payload"}]
    )
    ArenaRingMatch.objects.create(
        tenure_id=occupied["tenure_id"],
        attacker=b,
        defender=a,
        winner=a,
        battle_report=report,
        pair_low=min(a.pk, b.pk),
        pair_high=max(a.pk, b.pk),
        reward_date=timezone.localdate(),
        created_at=now,
    )
    with CaptureQueriesContext(connection) as queries:
        context = get_ring_event_detail_context(a, occupied["tenure_id"])
        matches = list(context["ring_event_matches"])
        assert matches[0].battle_report_id == report.pk
    selects = [row["sql"] for row in queries if "SELECT" in row["sql"] and "gameplay_arenaringmatch" in row["sql"]]
    assert selects
    assert all("battle_battlereport" not in sql for sql in selects)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5])
def test_ring_retention_rejects_invalid_batch_size(invalid):
    with pytest.raises(ValueError, match="positive integer"):
        cleanup_ring_history(batch_size=invalid)
