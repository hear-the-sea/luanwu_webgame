"""Published V2 targets that are keyed by virtual-player prestige band."""

from __future__ import annotations

from collections.abc import Mapping


class VirtualPrestigeTargetPolicyError(ValueError):
    """护院或建筑的声望段目标策略格式无效。"""


def starter_snapshot_profile_for_prestige_band(
    *,
    policy_payload: Mapping[str, object],
    prestige_band: str,
) -> Mapping[str, object]:
    """Return the published starter snapshot for one V2 prestige band."""

    starter_snapshots = policy_payload.get("starter_snapshots")
    if not isinstance(starter_snapshots, Mapping):
        raise VirtualPrestigeTargetPolicyError("policy starter_snapshots must be a mapping")
    profiles = starter_snapshots.get("profiles")
    if not isinstance(profiles, Mapping):
        raise VirtualPrestigeTargetPolicyError("policy starter_snapshots.profiles must be a mapping")
    profile = profiles.get(str(prestige_band))
    if not isinstance(profile, Mapping):
        raise VirtualPrestigeTargetPolicyError(
            f"policy starter_snapshots has no profile for prestige band {prestige_band!r}"
        )
    return profile


def virtual_juxianzhuang_level_for_prestige_band(
    *,
    policy_payload: Mapping[str, object],
    prestige_band: str,
) -> int:
    """Return the explicit 聚贤庄 target for a V2 prestige band.

    ``core_building_level`` is a strength component and is not guaranteed to
    identify 聚贤庄.  Older immutable policy releases predate the explicit
    field, so they retain a compatibility fallback; all current published
    policy payloads must carry ``juxianzhuang_level`` and are validated as
    such.
    """

    profile = starter_snapshot_profile_for_prestige_band(
        policy_payload=policy_payload,
        prestige_band=prestige_band,
    )
    raw_level = profile.get("juxianzhuang_level")
    field_name = "juxianzhuang_level"
    if raw_level is None:
        raw_level = profile.get("core_building_level")
        field_name = "juxianzhuang_level (legacy core_building_level fallback)"
    if isinstance(raw_level, bool) or not isinstance(raw_level, int) or raw_level < 1:
        raise VirtualPrestigeTargetPolicyError(
            f"policy starter_snapshots profile {field_name} must be a positive integer"
        )
    return int(raw_level)


def virtual_core_building_level_for_prestige_band(
    *,
    policy_payload: Mapping[str, object],
    prestige_band: str,
) -> int:
    """Compatibility alias for callers that used the former helper name."""

    return virtual_juxianzhuang_level_for_prestige_band(
        policy_payload=policy_payload,
        prestige_band=prestige_band,
    )


__all__ = [
    "VirtualPrestigeTargetPolicyError",
    "starter_snapshot_profile_for_prestige_band",
    "virtual_core_building_level_for_prestige_band",
    "virtual_juxianzhuang_level_for_prestige_band",
]
