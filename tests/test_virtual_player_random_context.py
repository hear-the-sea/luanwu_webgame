from __future__ import annotations

import math

import pytest

from gameplay.services.virtual_player_core.random_context import (
    RandomContext,
    UnsupportedRandomDomainError,
    UnsupportedRngVersionError,
    canonical_json_bytes,
    policy_rollout_bucket,
)


def _context(**overrides) -> RandomContext:
    values = {
        "rng_version": 1,
        "growth_seed": 123456,
        "engine_version": 2,
        "plan_schema_version": 1,
        "policy_version": 1,
        "maintenance_sequence": 7,
    }
    values.update(overrides)
    return RandomContext(**values)


def test_canonical_json_encoding_is_order_independent_and_compact() -> None:
    first = canonical_json_bytes({"z": 1, "nested": {"b": 2, "a": "春秋"}})
    second = canonical_json_bytes({"nested": {"a": "春秋", "b": 2}, "z": 1})

    assert first == second == b'{"nested":{"a":"\xe6\x98\xa5\xe7\xa7\x8b","b":2},"z":1}'


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"invalid": math.inf})


def test_random_context_has_a_frozen_digest_vector() -> None:
    digest = _context().digest(domain="roster", discriminator={"guest_key": "hero_a", "slot": 2})

    assert digest.hex() == "d353d4a7c054069f8d505d9d0aa19c9affae6b0bb13f6ddf7de404e39dd64c33"


def test_domains_and_sequences_have_independent_random_substreams() -> None:
    context = _context()

    roster = context.seed(domain="roster", discriminator="primary")
    gear = context.seed(domain="gear", discriminator="primary")
    next_roster = _context(maintenance_sequence=8).seed(domain="roster", discriminator="primary")

    assert len({roster, gear, next_roster}) == 3
    assert (
        context.random(domain="roster", discriminator="primary").random()
        == context.random(
            domain="roster",
            discriminator="primary",
        ).random()
    )


def test_policy_bucket_uses_the_same_versioned_digest() -> None:
    context = _context()

    assert context.bucket(domain="policy_rollout", discriminator=42) == 31
    assert context.bucket(domain="policy_rollout", discriminator=42, bucket_count=10) == 1


def test_policy_rollout_bucket_has_a_frozen_profile_and_target_vector() -> None:
    assert policy_rollout_bucket(profile_id=42, target_policy_version=2) == 36
    assert policy_rollout_bucket(profile_id=42, target_policy_version=3) == 47
    assert policy_rollout_bucket(profile_id=43, target_policy_version=2) == 40
    assert (
        policy_rollout_bucket(
            profile_id=42,
            target_policy_version=2,
            bucket_count=10,
        )
        == 6
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"profile_id": 0, "target_policy_version": 1},
        {"profile_id": True, "target_policy_version": 1},
        {"profile_id": 1, "target_policy_version": 0},
        {"profile_id": 1, "target_policy_version": True},
        {"profile_id": 1, "target_policy_version": 1, "bucket_count": 0},
    ],
)
def test_policy_rollout_bucket_rejects_invalid_identity(kwargs) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        policy_rollout_bucket(**kwargs)


def test_unsupported_rng_version_fails_closed() -> None:
    with pytest.raises(UnsupportedRngVersionError, match="RNG version: 2"):
        _context(rng_version=2).digest(domain="bootstrap", discriminator="profile")


@pytest.mark.parametrize("domain", ["", " ", "unknown", "Roster"])
def test_unknown_domains_are_rejected(domain: str) -> None:
    context = _context()

    with pytest.raises(UnsupportedRandomDomainError, match="random domain"):
        context.digest(domain=domain, discriminator="profile")


def test_invalid_bucket_count_is_rejected() -> None:
    context = _context()

    with pytest.raises(ValueError, match="bucket_count must be positive"):
        context.bucket(domain="policy_rollout", discriminator="profile", bucket_count=0)
