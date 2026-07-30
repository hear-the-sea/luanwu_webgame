from __future__ import annotations

import hmac
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from hashlib import blake2b, sha256
from typing import Any

from django.conf import settings
from django.db.models import Count, IntegerField, Max, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from gameplay.constants import BuildingKeys
from gameplay.models import BotProfile, Building, Manor, PlayerTroop
from guests.models import Guest

from .config import load_virtual_player_config
from .contracts import BotProjectionConfig
from .legacy.projection import apply_combat_persona, apply_stable_troop_variation, choose_strength_quantile
from .legacy.projection import nearest_rank_quantile as legacy_nearest_rank_quantile
from .projection import (
    ProjectionRuleError,
    ReferenceCandidate,
    ReferenceSelection,
    StrengthSummary,
    calculate_guest_arena_power,
)
from .projection import nearest_rank_quantile as strength_nearest_rank_quantile
from .projection import select_reference
from .random_context import RandomContext
from .selectors import band_filter_kwargs, prestige_bands, profile_target_prestige_band

CORE_BUILDING_KEYS = (
    BuildingKeys.SILVER_VAULT,
    BuildingKeys.GRANARY,
    BuildingKeys.JUXIAN_ZHUANG,
    BuildingKeys.JIADING_FANG,
    BuildingKeys.YOUXIA_BAOTA,
    BuildingKeys.LIANGGONG_CHANG,
)
HUMAN_REFERENCE_SNAPSHOT_VERSION = 1
HUMAN_REFERENCE_ACTIVE_WINDOW_DAYS = 30
HUMAN_REFERENCE_CANDIDATE_LIMIT = 128


class ReferenceSnapshotError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HumanReferenceSnapshot:
    snapshot_version: int
    business_key: str
    region: str
    prestige_band: str
    prestige: int
    core_building_level: int
    guest_count: int
    max_guest_level: int
    arena_lineup_power: int
    troop_total: int
    strength: StrengthSummary

    def as_candidate(self) -> ReferenceCandidate:
        return ReferenceCandidate(
            business_key=self.business_key,
            prestige_band=self.prestige_band,
            strength=self.strength,
            features={
                "core_building_level": self.core_building_level,
                "guest_count": self.guest_count,
                "max_guest_level": self.max_guest_level,
            },
        )


@dataclass(frozen=True, slots=True)
class HumanReferenceCohort:
    snapshot_version: int
    region: str
    prestige_band: str
    local_sample_count: int
    local_snapshots: tuple[HumanReferenceSnapshot, ...]
    global_same_band_snapshots: tuple[HumanReferenceSnapshot, ...]

    @property
    def local_candidates(self) -> tuple[ReferenceCandidate, ...]:
        return tuple(snapshot.as_candidate() for snapshot in self.local_snapshots)

    @property
    def global_same_band_candidates(self) -> tuple[ReferenceCandidate, ...]:
        return tuple(snapshot.as_candidate() for snapshot in self.global_same_band_snapshots)

    @property
    def global_same_band_cap(self) -> StrengthSummary | None:
        candidates = self.global_same_band_candidates
        if not candidates:
            return None
        return strength_quantile_summary(
            tuple(candidate.strength for candidate in candidates),
            quantile=0.95,
        )


def build_strength_summary(
    *,
    prestige: int,
    core_building_level: int,
    guest_count: int,
    max_guest_level: int,
    arena_lineup_power: int,
    troop_total: int,
) -> StrengthSummary:
    components = {
        "arena_lineup_power": max(0, int(arena_lineup_power)),
        "core_building_level": max(0, int(core_building_level)),
        "guest_count": max(0, int(guest_count)),
        "max_guest_level": max(0, int(max_guest_level)),
        "prestige": max(0, int(prestige)),
        "troop_total": max(0, int(troop_total)),
    }
    return StrengthSummary(
        composite=float(components["arena_lineup_power"] + 2 * components["troop_total"]),
        components=components,
    )


def starter_snapshot_int(
    snapshot: Mapping[str, Any],
    field: str,
    *,
    minimum: int = 0,
) -> int:
    raw = snapshot.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise ReferenceSnapshotError(f"starter snapshot {field} must be an integer >= {minimum}")
    return raw


def starter_snapshot_strength(snapshot: Mapping[str, Any]) -> StrengthSummary:
    summary = build_strength_summary(
        prestige=starter_snapshot_int(snapshot, "prestige"),
        core_building_level=starter_snapshot_int(snapshot, "core_building_level"),
        guest_count=starter_snapshot_int(snapshot, "guest_count"),
        max_guest_level=starter_snapshot_int(snapshot, "max_guest_level"),
        arena_lineup_power=starter_snapshot_int(snapshot, "arena_lineup_power"),
        troop_total=starter_snapshot_int(snapshot, "troop_total"),
    )
    declared_composite = starter_snapshot_int(snapshot, "composite_strength")
    if summary.composite != float(declared_composite):
        raise ReferenceSnapshotError("starter snapshot composite_strength does not match strength score version 1")
    return summary


def policy_starter_snapshot(
    policy_payload: Mapping[str, Any],
    *,
    prestige_band: str,
) -> tuple[int, Mapping[str, Any]]:
    starter_snapshots = policy_payload.get("starter_snapshots")
    if not isinstance(starter_snapshots, Mapping):
        raise ReferenceSnapshotError("policy starter_snapshots must be a mapping")
    snapshot_version = starter_snapshots.get("snapshot_version")
    if isinstance(snapshot_version, bool) or not isinstance(snapshot_version, int) or snapshot_version <= 0:
        raise ReferenceSnapshotError("policy starter_snapshots.snapshot_version must be a positive integer")
    profiles = starter_snapshots.get("profiles")
    if not isinstance(profiles, Mapping):
        raise ReferenceSnapshotError("policy starter_snapshots.profiles must be a mapping")
    snapshot = profiles.get(prestige_band)
    if not isinstance(snapshot, Mapping):
        raise ReferenceSnapshotError(f"policy has no starter snapshot for {prestige_band!r}")
    return snapshot_version, snapshot


def strength_quantile_summary(
    summaries: tuple[StrengthSummary, ...],
    *,
    quantile: float,
) -> StrengthSummary:
    if not summaries:
        raise ValueError("strength quantile requires at least one summary")
    component_names = tuple(summaries[0].components)
    if any(tuple(summary.components) != component_names for summary in summaries):
        raise ValueError("strength summary component keys must match")
    return StrengthSummary(
        composite=strength_nearest_rank_quantile([summary.composite for summary in summaries], quantile),
        components={
            component_name: strength_nearest_rank_quantile(
                [summary.components[component_name] for summary in summaries],
                quantile,
            )
            for component_name in component_names
        },
    )


def _human_reference_business_key(*, manor_id: int, snapshot_version: int) -> str:
    digest = hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        f"human-reference:{snapshot_version}:{int(manor_id)}".encode("ascii"),
        sha256,
    ).hexdigest()
    return f"human-ref-v{snapshot_version}:{digest}"


def _guest_arena_power(row: Mapping[str, Any]) -> int:
    return calculate_guest_arena_power(
        force=int(row["force"] or 0),
        intellect=int(row["intellect"] or 0),
        defense=int(row["defense_stat"] or 0),
        hp_bonus=int(row["hp_bonus"] or 0),
        archetype=str(row["template__archetype"] or ""),
        base_hp=int(row["template__base_hp"] or 0),
    )


def _stable_reference_sample(
    rows: Sequence[Mapping[str, Any]],
    *,
    snapshot_version: int,
    limit: int,
) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda row: _human_reference_business_key(
            manor_id=int(row["id"]),
            snapshot_version=snapshot_version,
        ),
    )[:limit]


def _load_human_reference_snapshots(
    rows: Sequence[Mapping[str, Any]],
    *,
    prestige_band: str,
    snapshot_version: int,
) -> tuple[HumanReferenceSnapshot, ...]:
    manor_ids = [int(row["id"]) for row in rows]
    if not manor_ids:
        return ()

    building_levels = {
        int(row["manor_id"]): int(row["level"] or 0)
        for row in Building.objects.filter(
            manor_id__in=manor_ids,
            building_type__key__in=CORE_BUILDING_KEYS,
        )
        .values("manor_id")
        .annotate(level=Max("level"))
    }
    troop_totals = {
        int(row["manor_id"]): int(row["total"] or 0)
        for row in PlayerTroop.objects.filter(manor_id__in=manor_ids).values("manor_id").annotate(total=Sum("count"))
    }
    guest_summaries: dict[int, dict[str, int]] = {
        manor_id: {"count": 0, "max_level": 0, "arena_power": 0} for manor_id in manor_ids
    }
    for guest_row in Guest.objects.filter(manor_id__in=manor_ids).values(
        "manor_id",
        "level",
        "force",
        "intellect",
        "defense_stat",
        "hp_bonus",
        "template__archetype",
        "template__base_hp",
    ):
        summary = guest_summaries[int(guest_row["manor_id"])]
        summary["count"] += 1
        summary["max_level"] = max(summary["max_level"], int(guest_row["level"] or 0))
        summary["arena_power"] += _guest_arena_power(guest_row)

    snapshots: list[HumanReferenceSnapshot] = []
    for manor_row in rows:
        manor_id = int(manor_row["id"])
        guest_summary = guest_summaries[manor_id]
        strength = build_strength_summary(
            prestige=int(manor_row["prestige"] or 0),
            core_building_level=building_levels.get(manor_id, 0),
            guest_count=guest_summary["count"],
            max_guest_level=guest_summary["max_level"],
            arena_lineup_power=guest_summary["arena_power"],
            troop_total=troop_totals.get(manor_id, 0),
        )
        snapshots.append(
            HumanReferenceSnapshot(
                snapshot_version=snapshot_version,
                business_key=_human_reference_business_key(
                    manor_id=manor_id,
                    snapshot_version=snapshot_version,
                ),
                region=str(manor_row["region"]),
                prestige_band=prestige_band,
                prestige=int(manor_row["prestige"] or 0),
                core_building_level=building_levels.get(manor_id, 0),
                guest_count=guest_summary["count"],
                max_guest_level=guest_summary["max_level"],
                arena_lineup_power=guest_summary["arena_power"],
                troop_total=troop_totals.get(manor_id, 0),
                strength=strength,
            )
        )
    return tuple(sorted(snapshots, key=lambda snapshot: snapshot.business_key))


def load_human_reference_cohort(
    *,
    region: str,
    prestige_band: str,
    low: int,
    high: int | None,
    now,
    snapshot_version: int = HUMAN_REFERENCE_SNAPSHOT_VERSION,
    candidate_limit: int = HUMAN_REFERENCE_CANDIDATE_LIMIT,
) -> HumanReferenceCohort:
    if timezone.is_naive(now):
        raise ValueError("now must be timezone-aware")
    if not isinstance(snapshot_version, int) or isinstance(snapshot_version, bool) or snapshot_version <= 0:
        raise ValueError("snapshot_version must be a positive integer")
    if not isinstance(candidate_limit, int) or isinstance(candidate_limit, bool) or candidate_limit <= 0:
        raise ValueError("candidate_limit must be a positive integer")

    rows = list(
        Manor.objects.filter(
            bot_profile__isnull=True,
            user__is_staff=False,
            user__is_superuser=False,
            last_active_at__gte=now - timedelta(days=HUMAN_REFERENCE_ACTIVE_WINDOW_DAYS),
            buildings__building_type__key__in=CORE_BUILDING_KEYS,
            guests__isnull=False,
            **band_filter_kwargs(int(low), None if high is None else int(high)),
        )
        .values("id", "region", "prestige")
        .distinct()
    )
    local_rows = [row for row in rows if str(row["region"]) == str(region)]
    local_sample_count = len(local_rows)
    if local_rows:
        selected_local = _stable_reference_sample(
            local_rows,
            snapshot_version=snapshot_version,
            limit=candidate_limit,
        )
        selected_global: list[Mapping[str, Any]] = []
    else:
        selected_local = []
        selected_global = _stable_reference_sample(
            rows,
            snapshot_version=snapshot_version,
            limit=candidate_limit,
        )
    selected_rows = [*selected_local, *selected_global]
    snapshots = _load_human_reference_snapshots(
        selected_rows,
        prestige_band=prestige_band,
        snapshot_version=snapshot_version,
    )
    local_keys = {
        _human_reference_business_key(
            manor_id=int(row["id"]),
            snapshot_version=snapshot_version,
        )
        for row in selected_local
    }
    return HumanReferenceCohort(
        snapshot_version=snapshot_version,
        region=str(region),
        prestige_band=str(prestige_band),
        local_sample_count=local_sample_count,
        local_snapshots=tuple(snapshot for snapshot in snapshots if snapshot.business_key in local_keys),
        global_same_band_snapshots=tuple(snapshot for snapshot in snapshots if snapshot.business_key not in local_keys),
    )


def select_policy_reference(
    *,
    policy_payload: Mapping[str, Any],
    context: RandomContext,
    region: str,
    prestige_band: str,
    band_lower_inclusive: int,
    band_upper_exclusive: int | None,
    now,
    calibrated_candidates: Sequence[ReferenceCandidate] | None = None,
    calibrated_sample_count: int | None = None,
) -> tuple[Mapping[str, Any], StrengthSummary, ReferenceSelection]:
    """Load and select the shared Bootstrap/Maintenance reference contract."""
    snapshot_version, snapshot = policy_starter_snapshot(
        policy_payload,
        prestige_band=prestige_band,
    )
    starter_strength = starter_snapshot_strength(snapshot)
    if calibrated_candidates is None:
        if calibrated_sample_count is not None:
            raise ReferenceSnapshotError("calibrated_sample_count requires calibrated_candidates")
        cohort = load_human_reference_cohort(
            region=region,
            prestige_band=prestige_band,
            low=band_lower_inclusive,
            high=band_upper_exclusive,
            now=now,
            snapshot_version=snapshot_version,
        )
        local_candidates = cohort.local_candidates
        local_sample_count = cohort.local_sample_count
        global_candidates = cohort.global_same_band_candidates
        global_same_band_cap = cohort.global_same_band_cap
    else:
        if (
            isinstance(calibrated_sample_count, bool)
            or not isinstance(calibrated_sample_count, int)
            or calibrated_sample_count < 0
        ):
            raise ReferenceSnapshotError("calibrated_sample_count must be a non-negative integer")
        local_candidates = tuple(calibrated_candidates)
        local_sample_count = calibrated_sample_count
        global_candidates = ()
        global_same_band_cap = None

    anchor_k = policy_payload.get("anchor_k")
    if isinstance(anchor_k, bool) or not isinstance(anchor_k, int) or anchor_k <= 0:
        raise ReferenceSnapshotError("policy anchor_k must be a positive integer")
    try:
        selection = select_reference(
            context=context,
            prestige_band=prestige_band,
            target_features={
                "core_building_level": starter_snapshot_int(snapshot, "core_building_level"),
                "guest_count": starter_snapshot_int(snapshot, "guest_count"),
                "max_guest_level": starter_snapshot_int(snapshot, "max_guest_level"),
            },
            starter_strength=starter_strength,
            local_candidates=local_candidates,
            local_sample_count=local_sample_count,
            global_candidates=global_candidates,
            global_same_band_cap=global_same_band_cap,
            nearest_k=anchor_k,
        )
    except ProjectionRuleError as exc:
        raise ReferenceSnapshotError(str(exc)) from exc
    return snapshot, starter_strength, selection


def load_manor_strength_summaries(
    *,
    manor_ids: Sequence[int],
    guests_by_manor: Mapping[int, Sequence[Guest]] | None = None,
) -> dict[int, StrengthSummary]:
    normalized_ids = tuple(dict.fromkeys(int(manor_id) for manor_id in manor_ids))
    if not normalized_ids:
        return {}
    building_level = (
        Building.objects.filter(
            manor_id=OuterRef("pk"),
            building_type__key__in=CORE_BUILDING_KEYS,
        )
        .order_by()
        .values("manor_id")
        .annotate(value=Max("level"))
        .values("value")[:1]
    )
    troop_total = (
        PlayerTroop.objects.filter(manor_id=OuterRef("pk"))
        .order_by()
        .values("manor_id")
        .annotate(value=Sum("count"))
        .values("value")[:1]
    )
    rows = tuple(
        Manor.objects.filter(pk__in=normalized_ids)
        .annotate(
            core_building_level=Coalesce(
                Subquery(building_level, output_field=IntegerField()),
                Value(0),
            ),
            troop_total=Coalesce(
                Subquery(troop_total, output_field=IntegerField()),
                Value(0),
            ),
        )
        .values(
            "id",
            "region",
            "prestige",
            "core_building_level",
            "troop_total",
        )
        .order_by("id")
    )
    guest_summaries = {manor_id: {"count": 0, "max_level": 0, "arena_power": 0} for manor_id in normalized_ids}
    if guests_by_manor is None:
        for guest_row in Guest.objects.filter(manor_id__in=normalized_ids).values(
            "manor_id",
            "level",
            "force",
            "intellect",
            "defense_stat",
            "hp_bonus",
            "template__archetype",
            "template__base_hp",
        ):
            summary = guest_summaries[int(guest_row["manor_id"])]
            summary["count"] += 1
            summary["max_level"] = max(
                summary["max_level"],
                int(guest_row["level"] or 0),
            )
            summary["arena_power"] += _guest_arena_power(guest_row)
    else:
        for manor_id, guests in guests_by_manor.items():
            normalized_manor_id = int(manor_id)
            if normalized_manor_id not in guest_summaries:
                continue
            summary = guest_summaries[normalized_manor_id]
            for guest in guests:
                if int(guest.manor_id) != normalized_manor_id:
                    raise ReferenceSnapshotError("strength snapshot guest belongs to another Manor")
                summary["count"] += 1
                summary["max_level"] = max(
                    summary["max_level"],
                    int(guest.level or 0),
                )
                summary["arena_power"] += calculate_guest_arena_power(
                    force=int(guest.force or 0),
                    intellect=int(guest.intellect or 0),
                    defense=int(guest.defense_stat or 0),
                    hp_bonus=int(guest.hp_bonus or 0),
                    archetype=str(guest.template.archetype or ""),
                    base_hp=int(guest.template.base_hp or 0),
                )

    return {
        int(row["id"]): build_strength_summary(
            prestige=int(row["prestige"] or 0),
            core_building_level=int(row["core_building_level"] or 0),
            guest_count=guest_summaries[int(row["id"])]["count"],
            max_guest_level=guest_summaries[int(row["id"])]["max_level"],
            arena_lineup_power=guest_summaries[int(row["id"])]["arena_power"],
            troop_total=int(row["troop_total"] or 0),
        )
        for row in rows
    }


def load_manor_strength_summary(
    *,
    manor_id: int,
    guests: Sequence[Guest] | None = None,
) -> StrengthSummary:
    normalized_manor_id = int(manor_id)
    summaries = load_manor_strength_summaries(
        manor_ids=(normalized_manor_id,),
        guests_by_manor=(None if guests is None else {normalized_manor_id: tuple(guests)}),
    )
    try:
        return summaries[normalized_manor_id]
    except KeyError as exc:
        raise Manor.DoesNotExist from exc


def projection_from_real_players(
    *,
    region: str | None,
    low: int,
    high: int | None,
    rng: random.Random,
    config: dict[str, Any] | None = None,
    now=None,
    sample_seed: int | None = None,
    strength_quantile: str = "p50",
) -> BotProjectionConfig | None:
    config = config or load_virtual_player_config()
    now = now or timezone.now()
    projection_config = config.get("projection") or {}
    active_sample_days = max(1, int(projection_config.get("active_sample_days") or 30))
    filters = band_filter_kwargs(low, high)
    base_qs = Manor.objects.filter(
        bot_profile__isnull=True,
        user__is_staff=False,
        user__is_superuser=False,
        last_active_at__gte=now - timedelta(days=active_sample_days),
        **filters,
    )
    regional_qs = base_qs.filter(region=region) if region else Manor.objects.none()
    regional_min_sample_size = max(1, int(projection_config.get("regional_min_sample_size") or 5))
    qs = regional_qs if regional_qs.count() >= regional_min_sample_size else base_qs
    count = qs.count()
    if count <= 0:
        return None

    sample_size = max(1, int(projection_config.get("real_projection_sample_size") or 25))
    sample_size = min(sample_size, count)
    stable_seed = int(sample_seed if sample_seed is not None else rng.getrandbits(63))
    candidate_ids = list(qs.values_list("id", flat=True))
    candidate_ids.sort(
        key=lambda manor_id: blake2b(
            f"{stable_seed}:{int(manor_id)}".encode("ascii"),
            digest_size=8,
        ).digest()
    )
    selected_ids = candidate_ids[:sample_size]
    samples = list(
        qs.filter(id__in=selected_ids)
        .order_by("id")
        .annotate(
            sampled_building_level=Max(
                "buildings__level",
                filter=Q(buildings__building_type__key__in=CORE_BUILDING_KEYS),
            ),
            sampled_guest_count=Count("guests", distinct=True),
            sampled_guest_level=Max("guests__level"),
        )
        .values(
            "id",
            "prestige",
            "sampled_building_level",
            "sampled_guest_count",
            "sampled_guest_level",
        )
    )
    troop_totals = {
        row["manor_id"]: int(row["total"] or 0)
        for row in PlayerTroop.objects.filter(manor_id__in=selected_ids).values("manor_id").annotate(total=Sum("count"))
    }
    quantile_by_key = {"p25": 0.25, "p50": 0.50, "p75": 0.75}
    quantile = quantile_by_key.get(str(strength_quantile), 0.50)
    building_level = max(
        1,
        legacy_nearest_rank_quantile([int(row["sampled_building_level"] or 1) for row in samples], quantile),
    )
    guest_count = max(
        1,
        min(
            8,
            legacy_nearest_rank_quantile([int(row["sampled_guest_count"] or 1) for row in samples], quantile),
        ),
    )
    guest_level = max(
        1,
        legacy_nearest_rank_quantile(
            [int(row["sampled_guest_level"] or building_level) for row in samples],
            quantile,
        ),
    )
    troop_count = max(
        0,
        legacy_nearest_rank_quantile([troop_totals.get(int(row["id"]), 0) for row in samples], quantile),
    )
    prestige = legacy_nearest_rank_quantile([int(row["prestige"] or low) for row in samples], quantile)
    if prestige > 0:
        jitter_bps = max(0, int(projection_config.get("real_projection_jitter_bps") or 0))
        jitter = int(prestige * jitter_bps / 10_000)
        prestige += rng.randint(-jitter, jitter)
    upper = high if high is not None else max(low + 1, prestige + 1)
    prestige = max(low, min(max(low, upper - 1), prestige))

    return BotProjectionConfig(
        prestige=prestige,
        building_level=max(1, int(building_level)),
        guest_count=guest_count,
        guest_level=guest_level,
        troop_count=troop_count,
    )


def projection_for_band(
    band: str,
    low: int,
    high: int | None,
    rng: random.Random,
    *,
    region: str | None = None,
    config: dict[str, Any] | None = None,
    sample_seed: int | None = None,
    archetype: str | None = None,
) -> BotProjectionConfig:
    config = config or load_virtual_player_config()
    quantile_weights = (config.get("projection") or {}).get("strength_quantile_weights") or {
        "p25": 25,
        "p50": 50,
        "p75": 25,
    }
    strength_quantile = (
        "p25"
        if archetype == BotProfile.Archetype.ABANDONED
        else choose_strength_quantile(int(sample_seed), quantile_weights) if sample_seed is not None else "p50"
    )
    sampled = projection_from_real_players(
        region=region,
        low=low,
        high=high,
        rng=rng,
        config=config,
        sample_seed=sample_seed,
        strength_quantile=strength_quantile,
    )
    if sampled is not None:
        return sampled

    upper = high or max(low + 1, 50000)
    prestige = rng.randint(low, max(low, upper - 1))
    band_level = {
        "newbie": 2,
        "junior": 5,
        "middle": 9,
        "senior": 14,
        "veteran": 18,
    }.get(band, max(2, min(18, int((prestige / 2000) + 3))))
    guest_count = max(1, min(8, band_level // 2))
    guest_level = max(1, band_level + rng.randint(-1, 2))
    return BotProjectionConfig(
        prestige=prestige,
        building_level=band_level,
        guest_count=guest_count,
        guest_level=guest_level,
    )


def apply_persona_to_projection(
    projection: BotProjectionConfig,
    *,
    archetype: str,
    config: dict[str, Any],
    growth_seed: int,
) -> BotProjectionConfig:
    targets = apply_combat_persona(
        {
            "guest_level": projection.guest_level,
            "guest_count": projection.guest_count,
            "troop_count": projection.troop_count,
        },
        str(archetype),
        config=config.get("combat_personas") or {},
    )
    return BotProjectionConfig(
        prestige=projection.prestige,
        building_level=projection.building_level,
        guest_count=targets["guest_count"],
        guest_level=targets["guest_level"],
        troop_count=apply_stable_troop_variation(targets["troop_count"], growth_seed),
    )


def maintenance_projection_from_real_players(
    profile: BotProfile,
    *,
    rng: random.Random,
    config: dict[str, Any],
) -> BotProjectionConfig | None:
    band_range = prestige_bands(config).get(profile_target_prestige_band(profile))
    if band_range is None:
        return None
    low, high = band_range
    quantile_weights = (config.get("projection") or {}).get("strength_quantile_weights") or {
        "p25": 25,
        "p50": 50,
        "p75": 25,
    }
    strength_quantile = (
        "p25"
        if profile.archetype == BotProfile.Archetype.ABANDONED
        else choose_strength_quantile(profile.growth_seed, quantile_weights)
    )
    return projection_from_real_players(
        region=profile.manor.region,
        low=low,
        high=high,
        rng=rng,
        config=config,
        sample_seed=profile.growth_seed,
        strength_quantile=strength_quantile,
    )


__all__ = [
    "HUMAN_REFERENCE_ACTIVE_WINDOW_DAYS",
    "HUMAN_REFERENCE_CANDIDATE_LIMIT",
    "HUMAN_REFERENCE_SNAPSHOT_VERSION",
    "HumanReferenceCohort",
    "HumanReferenceSnapshot",
    "ReferenceSnapshotError",
    "apply_persona_to_projection",
    "build_strength_summary",
    "load_human_reference_cohort",
    "load_manor_strength_summaries",
    "load_manor_strength_summary",
    "maintenance_projection_from_real_players",
    "projection_for_band",
    "projection_from_real_players",
    "policy_starter_snapshot",
    "select_policy_reference",
    "starter_snapshot_int",
    "starter_snapshot_strength",
    "strength_quantile_summary",
]
