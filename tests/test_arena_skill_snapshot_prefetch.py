from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from gameplay.models import BotProfile, Manor
from gameplay.services.arena import snapshots
from gameplay.services.arena import virtual_reserve_pool as pool
from guests.models import Guest, GuestSkill, GuestStatus, Skill
from tests.arena_services.support import create_guest, create_guest_template

pytestmark = pytest.mark.django_db


def _previous_skill_keys(guest):
    skills = getattr(guest, "skills", None)
    values_list = getattr(skills, "values_list", None)
    if callable(values_list):
        return [str(key).strip() for key in values_list("key", flat=True) if str(key).strip()]
    return [str(key).strip() for key in (getattr(guest, "_override_skills", None) or []) if str(key).strip()]


def _previous_guest_prefetches():
    return (
        Prefetch(
            "manor__guests",
            queryset=Guest.objects.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"),
            to_attr="arena_idle_guests",
        ),
        Prefetch(
            "manor__guests",
            queryset=Guest.objects.only("id", "manor_id", "template_id").order_by("id"),
            to_attr="arena_all_guests",
        ),
    )


@pytest.fixture
def arena_skill_world():
    def build(pattern, *, profile_count=3, guests_per_profile=4):
        now = timezone.now()
        template = create_guest_template("arena-prefetch-template")
        # Skill keys, IDs and link insertion order deliberately differ. Never
        # assert a guessed ID/key ordering for the old unordered database read.
        Skill.objects.bulk_create([Skill(key=key, name=key) for key in ("prefetch_z", "prefetch_a", "prefetch_m")])
        skill_by_key = {skill.key: skill for skill in Skill.objects.filter(key__startswith="prefetch_")}
        skills = [skill_by_key[key] for key in ("prefetch_z", "prefetch_a", "prefetch_m")]
        profiles = []
        guests = []
        for profile_index in range(profile_count):
            username = f"arena-prefetch-user-{profile_index}"
            User.objects.bulk_create([User(username=username)])
            manor = Manor.objects.create(user=User.objects.get(username=username), region="north")
            profile = BotProfile.objects.create(
                manor=manor,
                engine_version=2,
                policy_version=2,
                rng_version=1,
                plan_schema_version=1,
                policy_checksum="a" * 64,
                last_strength_increase_at=now,
                v2_enrolled_at=now,
                prestige_band="newbie",
                target_prestige_band="newbie",
                current_prestige_band="newbie",
                growth_seed=manor.id,
                next_growth_at=now,
                abandon_at=now,
                retire_at=now,
            )
            profiles.append(profile)
            for guest_index in range(guests_per_profile):
                ordinal = len(guests)
                guest = create_guest(manor, template, f"-{profile_index}-{guest_index}")
                skill_count = pattern[ordinal % len(pattern)]
                selected = skills[:skill_count]
                if ordinal % 2:
                    selected = list(reversed(selected))
                GuestSkill.objects.bulk_create([GuestSkill(guest=guest, skill=skill) for skill in selected])
                guests.append(guest)
        return SimpleNamespace(profiles=profiles, guests=guests, skills=skills, now=now)

    return build


def _candidate_snapshots():
    profiles = list(
        pool._candidate_queryset(
            [BotProfile.State.ACTIVE],
            target_cell=("north", "newbie"),
            occupied_manor_ids=set(),
        )
    )
    return {profile.id: pool._profile_lineup_snapshots(profile) for profile in profiles}


def _previous_candidate_snapshots(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(pool, "_arena_candidate_guest_prefetches", _previous_guest_prefetches)
        patch.setattr(snapshots, "serialize_guest_skill_keys", _previous_skill_keys)
        return _candidate_snapshots()


@pytest.mark.parametrize("pattern", [(0,), (1,), (2,), (3,), (0, 1, 2, 3)])
def test_arena_skill_prefetch_preserves_complete_candidate_snapshots_and_query_counts(
    arena_skill_world, monkeypatch, pattern
):
    world = arena_skill_world(pattern)
    with CaptureQueriesContext(connection) as old_queries:
        previous = _previous_candidate_snapshots(monkeypatch)
    with CaptureQueriesContext(connection) as new_queries:
        current = _candidate_snapshots()
    assert current == previous
    multi_count = sum(pattern[index % len(pattern)] > 1 for index in range(len(world.guests)))
    assert len(old_queries) == 3 + len(world.guests)
    assert len(new_queries) == 4 + multi_count
    # Empty/single-key caches remove per-guest reads; every multi-key guest
    # retains exactly the previous values_list SQL, including implicit order.
    old_skill_sql = [query["sql"] for query in old_queries if "guests_skill" in query["sql"]]
    new_skill_sql = [query["sql"] for query in new_queries if "guests_skill" in query["sql"]]
    expected_fallback = [
        old_skill_sql[index] for index in range(len(world.guests)) if pattern[index % len(pattern)] > 1
    ]
    assert new_skill_sql[1:] == expected_fallback


def test_arena_skill_prefetch_preserves_uncached_and_lightweight_fallbacks():
    manager = SimpleNamespace(values_list=Mock(return_value=[" z ", "", "a"]))
    assert snapshots.serialize_guest_skill_keys(SimpleNamespace(skills=manager)) == ["z", "a"]
    manager.values_list.assert_called_once_with("key", flat=True)
    guest = SimpleNamespace(_override_skills=[" z ", "", "a"], skills=SimpleNamespace(all=lambda: []))
    assert snapshots.serialize_guest_skill_keys(guest) == _previous_skill_keys(guest) == ["z", "a"]
    proxy = snapshots.ArenaGuestSnapshotProxy(
        {
            "display_name": "proxy",
            "template_key": "proxy",
            "attack": 1,
            "defense": 1,
            "max_hp": 100,
            "skill_keys": [" z ", "a"],
        }
    )
    assert snapshots.serialize_guest_skill_keys(proxy) == _previous_skill_keys(proxy) == ["z", "a"]


@pytest.mark.parametrize("pattern", [(0,), (1,), (0, 1, 2, 3)])
def test_arena_skill_prefetch_candidate_iterator_batches_skill_reads(arena_skill_world, monkeypatch, pattern):
    world = arena_skill_world(pattern)

    def collect():
        candidates = pool._candidate_queryset(
            [BotProfile.State.ACTIVE],
            target_cell=("north", "newbie"),
            occupied_manor_ids=set(),
        )
        return {profile.id: pool._profile_lineup_snapshots(profile) for profile in candidates.iterator(chunk_size=2)}

    with monkeypatch.context() as patch:
        patch.setattr(pool, "_arena_candidate_guest_prefetches", _previous_guest_prefetches)
        patch.setattr(snapshots, "serialize_guest_skill_keys", _previous_skill_keys)
        with CaptureQueriesContext(connection) as previous_queries:
            previous = collect()
    with CaptureQueriesContext(connection) as current_queries:
        current = collect()
    multi_count = sum(pattern[index % len(pattern)] > 1 for index in range(len(world.guests)))
    assert current == previous
    assert len(previous_queries) == 1 + 2 * 2 + len(world.guests)
    assert len(current_queries) == 1 + 2 * 3 + multi_count


@pytest.mark.parametrize("skill_count", [0, 1, 3])
def test_arena_skill_prefetch_reads_cache_only_when_order_is_unique(arena_skill_world, skill_count):
    world = arena_skill_world((skill_count,), profile_count=1, guests_per_profile=1)
    guest = pool._arena_snapshot_guest_queryset().get(id=world.guests[0].id)
    with CaptureQueriesContext(connection) as queries:
        actual = snapshots.serialize_guest_skill_keys(guest)
    assert len(queries) == int(skill_count > 1)
    assert actual == _previous_skill_keys(Guest.objects.get(id=guest.id))


def test_arena_skill_prefetch_empty_lineup_has_no_skill_query(arena_skill_world):
    world = arena_skill_world((0,), profile_count=1, guests_per_profile=0)
    with CaptureQueriesContext(connection) as queries:
        assert pool._profile_lineup_snapshots(world.profiles[0]) == []
    assert len(queries) == 1


@pytest.mark.parametrize("original_count", [0, 1])
def test_arena_skill_prefetch_does_not_use_other_callers_ordinary_skill_cache(arena_skill_world, original_count):
    world = arena_skill_world((original_count,), profile_count=1, guests_per_profile=1)
    guest = Guest.objects.prefetch_related("skills").get(id=world.guests[0].id)
    original = list(guest.skills.all())
    if original_count:
        GuestSkill.objects.filter(guest=guest).update(skill=world.skills[1])
    else:
        GuestSkill.objects.bulk_create([GuestSkill(guest=guest, skill=world.skills[1])])
    assert list(guest.skills.all()) == original
    with CaptureQueriesContext(connection) as queries:
        result = snapshots.serialize_guest_skill_keys(guest)
    assert len(queries) == 1
    assert result == [world.skills[1].key]


def test_arena_skill_prefetch_lock_recheck_loads_new_skill_and_guest_state(arena_skill_world, monkeypatch):
    world = arena_skill_world((1,), profile_count=1, guests_per_profile=1)
    candidate = pool._candidate_queryset(
        [BotProfile.State.ACTIVE],
        target_cell=("north", "newbie"),
        occupied_manor_ids=set(),
    ).get(id=world.profiles[0].id)
    previous = pool._profile_lineup_snapshots(candidate)
    GuestSkill.objects.filter(guest=world.guests[0]).update(skill=world.skills[1])
    Guest.objects.filter(id=world.guests[0].id).update(force=999)
    seen = {}

    def assess(_demand, locked_profile):
        seen["profile"] = locked_profile
        seen["snapshots"] = pool._profile_lineup_snapshots(locked_profile)
        # Stop at the actual locked assessment boundary before persisting a
        # member. The lease must already have read fresh guests and skills.
        return SimpleNamespace(disposition=pool.ArenaReserveCandidateDisposition.REJECTED)

    monkeypatch.setattr(pool, "is_virtual_profile_arena_match_eligible", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pool, "assess_arena_reserve_candidate", assess)
    result = pool._lease_candidate(
        demand=SimpleNamespace(id=1),
        profile_id=candidate.id,
        allowed_states=[BotProfile.State.ACTIVE],
        member_state="ready",
        target_cell=("north", "newbie"),
        supply_band_priority=("newbie",),
        now=world.now,
    )
    assert result is None
    assert seen["profile"] is not candidate
    assert previous[0]["skill_keys"] == [world.skills[0].key]
    assert seen["snapshots"][0]["skill_keys"] == [world.skills[1].key]
    assert seen["snapshots"][0]["force"] == 999
    assert pool._profile_lineup_snapshots(candidate) == previous


@pytest.mark.parametrize("pattern", [(0,), (1,), (3,), (0, 1, 2, 3)])
def test_arena_skill_snapshot_prefetch_benchmark(arena_skill_world, monkeypatch, pattern):
    """Print warm paired results with -s, including all-multi prefetch cost."""
    world = arena_skill_world(pattern, profile_count=5, guests_per_profile=6)
    assert _candidate_snapshots() == _previous_candidate_snapshots(monkeypatch)
    elapsed = {"previous": [], "current": []}
    counts = {"previous": [], "current": []}
    for _repeat in range(5):
        results = {}
        for name in ("previous", "current"):
            with monkeypatch.context() as patch:
                if name == "previous":
                    patch.setattr(pool, "_arena_candidate_guest_prefetches", _previous_guest_prefetches)
                    patch.setattr(snapshots, "serialize_guest_skill_keys", _previous_skill_keys)
                with CaptureQueriesContext(connection) as queries:
                    started_at = perf_counter()
                    results[name] = _candidate_snapshots()
                    elapsed[name].append((perf_counter() - started_at) * 1000)
            counts[name].append(len(queries))
        assert results["current"] == results["previous"]
    multi_count = sum(pattern[index % len(pattern)] > 1 for index in range(len(world.guests)))
    assert counts["previous"] == [3 + len(world.guests)] * 5
    assert counts["current"] == [4 + multi_count] * 5
    print(
        json.dumps(
            {
                "benchmark": "arena_candidate_skill_snapshot_prefetch",
                "database_vendor": connection.vendor,
                "profiles": 5,
                "guests": len(world.guests),
                "skill_count_pattern": pattern,
                "multi_skill_guests": multi_count,
                "warm_repetitions": 5,
                "previous_queries": counts["previous"][0],
                "current_queries": counts["current"][0],
                "previous_median_ms": round(median(elapsed["previous"]), 3),
                "current_median_ms": round(median(elapsed["current"]), 3),
                "runtime_sha256": {
                    "gameplay/services/arena/snapshots.py": sha256(Path(snapshots.__file__).read_bytes()).hexdigest(),
                    "gameplay/services/arena/virtual_reserve_pool.py": sha256(
                        Path(pool.__file__).read_bytes()
                    ).hexdigest(),
                },
            },
            sort_keys=True,
        )
    )
