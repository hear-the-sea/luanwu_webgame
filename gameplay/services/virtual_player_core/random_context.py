from __future__ import annotations

import json
import random
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

RNG_VERSION_SHA256_V1 = 1
SUPPORTED_RNG_VERSIONS = frozenset({RNG_VERSION_SHA256_V1})
SUPPORTED_RANDOM_DOMAINS = frozenset(
    {
        "bootstrap",
        "buildings",
        "gear",
        "inventory",
        "lifecycle",
        "policy_rollout",
        "reference_anchor",
        "roster",
        "schedule",
        "skills",
        "technology",
        "training",
        "troops",
    }
)


class UnsupportedRngVersionError(ValueError):
    pass


class UnsupportedRandomDomainError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the versioned random-context payload with one stable JSON profile."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_rng_version(rng_version: int) -> int:
    normalized = int(rng_version)
    if normalized not in SUPPORTED_RNG_VERSIONS:
        raise UnsupportedRngVersionError(f"Unsupported virtual-player RNG version: {normalized}")
    return normalized


def derive_digest(
    *,
    rng_version: int,
    growth_seed: int,
    engine_version: int,
    plan_schema_version: int,
    policy_version: int,
    maintenance_sequence: int,
    domain: str,
    discriminator: str | int | Mapping[str, Any],
) -> bytes:
    normalized_rng_version = _validate_rng_version(rng_version)
    normalized_domain = str(domain).strip()
    if normalized_domain not in SUPPORTED_RANDOM_DOMAINS:
        raise UnsupportedRandomDomainError(f"Unsupported virtual-player random domain: {normalized_domain!r}")
    payload = {
        "discriminator": discriminator,
        "domain": normalized_domain,
        "engine_version": int(engine_version),
        "growth_seed": int(growth_seed),
        "maintenance_sequence": int(maintenance_sequence),
        "namespace": "virtual-player",
        "plan_schema_version": int(plan_schema_version),
        "policy_version": int(policy_version),
        "rng_version": normalized_rng_version,
    }
    return sha256(canonical_json_bytes(payload)).digest()


def policy_rollout_bucket(
    *,
    profile_id: int,
    target_policy_version: int,
    bucket_count: int = 100,
) -> int:
    normalized_profile_id = int(profile_id)
    normalized_target_version = int(target_policy_version)
    normalized_bucket_count = int(bucket_count)
    if isinstance(profile_id, bool) or normalized_profile_id < 1:
        raise ValueError("profile_id must be a positive integer")
    if isinstance(target_policy_version, bool) or normalized_target_version < 1:
        raise ValueError("target_policy_version must be a positive integer")
    if isinstance(bucket_count, bool) or normalized_bucket_count < 1:
        raise ValueError("bucket_count must be a positive integer")
    digest = derive_digest(
        rng_version=RNG_VERSION_SHA256_V1,
        growth_seed=0,
        engine_version=2,
        plan_schema_version=0,
        policy_version=normalized_target_version,
        maintenance_sequence=0,
        domain="policy_rollout",
        discriminator={
            "profile_id": normalized_profile_id,
            "target_policy_version": normalized_target_version,
        },
    )
    return int.from_bytes(digest, "big") % normalized_bucket_count


@dataclass(frozen=True, slots=True)
class RandomContext:
    rng_version: int
    growth_seed: int
    engine_version: int
    plan_schema_version: int
    policy_version: int
    maintenance_sequence: int

    def digest(self, *, domain: str, discriminator: str | int | Mapping[str, Any]) -> bytes:
        return derive_digest(
            rng_version=self.rng_version,
            growth_seed=self.growth_seed,
            engine_version=self.engine_version,
            plan_schema_version=self.plan_schema_version,
            policy_version=self.policy_version,
            maintenance_sequence=self.maintenance_sequence,
            domain=domain,
            discriminator=discriminator,
        )

    def seed(self, *, domain: str, discriminator: str | int | Mapping[str, Any]) -> int:
        return int.from_bytes(self.digest(domain=domain, discriminator=discriminator), "big")

    def random(self, *, domain: str, discriminator: str | int | Mapping[str, Any]) -> random.Random:
        return random.Random(self.seed(domain=domain, discriminator=discriminator))

    def bucket(
        self,
        *,
        domain: str,
        discriminator: str | int | Mapping[str, Any],
        bucket_count: int = 100,
    ) -> int:
        normalized_count = int(bucket_count)
        if normalized_count <= 0:
            raise ValueError("bucket_count must be positive")
        return self.seed(domain=domain, discriminator=discriminator) % normalized_count


__all__ = [
    "RNG_VERSION_SHA256_V1",
    "RandomContext",
    "SUPPORTED_RANDOM_DOMAINS",
    "SUPPORTED_RNG_VERSIONS",
    "UnsupportedRandomDomainError",
    "UnsupportedRngVersionError",
    "canonical_json_bytes",
    "derive_digest",
    "policy_rollout_bucket",
]
