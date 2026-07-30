from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Iterator

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.config import GUEST
from gameplay.models import BotProfile, Building, Manor, PlayerTroop
from gameplay.services.virtual_player_core.population_runtime import virtual_player_prestige_bands
from guests.models import GearItem, Guest, GuestRarity, GuestSkill

BASELINE_SCHEMA_VERSION = 2
DEFAULT_SAMPLE_LIMIT = 1000
DEFAULT_MINIMUM_PROFILE_SAMPLE = 30
DEFAULT_MINIMUM_PROFILES_PER_BAND = DEFAULT_MINIMUM_PROFILE_SAMPLE
OUTLIER_MINIMUM_SAMPLE = 5
ROBUST_Z_THRESHOLD = 3.5


@dataclass
class _QueryCounter:
    count: int = 0

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        return execute(sql, params, many, context)


@contextmanager
def _consistent_read_snapshot() -> Iterator[dict[str, str]]:
    if connection.in_atomic_block:
        raise ValueError("baseline audit must run outside an existing transaction to establish a consistent snapshot")

    vendor = str(connection.vendor)
    if vendor not in {"mysql", "postgresql", "sqlite"}:
        raise ValueError(f"baseline audit does not define snapshot semantics for database vendor {vendor!r}")

    with transaction.atomic():
        if vendor in {"mysql", "postgresql"}:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            isolation = "repeatable_read"
        else:
            isolation = "sqlite_transaction_snapshot"
        yield {
            "database_vendor": vendor,
            "isolation": isolation,
            "read_only": "enforced_by_query_audit",
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nearest_rank(values: Iterable[float | int], percentile: float) -> float | int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(float(percentile) * len(ordered)) - 1))
    return ordered[index]


def _numeric_summary(values: Iterable[float | int], *, digits: int = 6) -> dict[str, float | int | None]:
    normalized = list(values)
    if not normalized:
        return {"mean": None, "p10": None, "p50": None, "p90": None}
    mean_value = sum(float(value) for value in normalized) / len(normalized)
    return {
        "mean": round(mean_value, digits),
        "p10": _nearest_rank(normalized, 0.10),
        "p50": _nearest_rank(normalized, 0.50),
        "p90": _nearest_rank(normalized, 0.90),
    }


def _ratio_map(values: Iterable[str]) -> dict[str, float]:
    counts = Counter(str(value) for value in values)
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {key: round(count / total, 6) for key, count in sorted(counts.items())}


def _collision_rate(signatures: Iterable[Any]) -> float:
    fingerprints = [_checksum(signature) for signature in signatures]
    if not fingerprints:
        return 0.0
    return round((len(fingerprints) - len(set(fingerprints))) / len(fingerprints), 6)


def _troop_concentration(troops: dict[str, int]) -> float:
    total = sum(max(0, int(count)) for count in troops.values())
    if total <= 0:
        return 0.0
    return round(sum((max(0, int(count)) / total) ** 2 for count in troops.values()), 6)


def _robust_joint_outlier_rate(records: list[dict[str, Any]]) -> float | None:
    if len(records) < OUTLIER_MINIMUM_SAMPLE:
        return None
    feature_names = (
        "guest_count",
        "mean_guest_level",
        "guest_level_gap",
        "gear_count",
        "skill_count",
        "troop_total",
        "troop_concentration",
        "mean_building_level",
    )
    centers: dict[str, float] = {}
    deviations: dict[str, float] = {}
    for feature_name in feature_names:
        values = [float(record["features"][feature_name]) for record in records]
        center = float(median(values))
        centers[feature_name] = center
        deviations[feature_name] = float(median(abs(value - center) for value in values))

    outlier_count = 0
    for record in records:
        is_outlier = False
        for feature_name in feature_names:
            deviation = deviations[feature_name]
            if deviation <= 0:
                continue
            robust_z = 0.6745 * abs(float(record["features"][feature_name]) - centers[feature_name]) / deviation
            if robust_z > ROBUST_Z_THRESHOLD:
                is_outlier = True
                break
        outlier_count += int(is_outlier)
    return round(outlier_count / len(records), 6)


def _prestige_band(prestige: int, bands: dict[str, tuple[int, int | None]]) -> str:
    value = int(prestige or 0)
    for name, (low, high) in bands.items():
        if value >= int(low) and (high is None or value < int(high)):
            return str(name)
    return "unclassified"


def _base_profile_record(*, manor_id: int, region: str, prestige: int, band: str) -> dict[str, Any]:
    return {
        "manor_id": int(manor_id),
        "region": str(region),
        "prestige": int(prestige or 0),
        "prestige_band": str(band),
        "guests": [],
        "gear": [],
        "skills": [],
        "troops": {},
        "buildings": {},
    }


def _load_profile_records(
    manor_rows: list[dict[str, Any]],
    *,
    bands: dict[str, tuple[int, int | None]],
) -> list[dict[str, Any]]:
    records_by_manor = {
        int(row["id"]): _base_profile_record(
            manor_id=int(row["id"]),
            region=str(row["region"]),
            prestige=int(row["prestige"] or 0),
            band=_prestige_band(int(row["prestige"] or 0), bands),
        )
        for row in manor_rows
    }
    manor_ids = sorted(records_by_manor)
    if not manor_ids:
        return []

    guest_to_manor: dict[int, int] = {}
    for guest_row in (
        Guest.objects.filter(manor_id__in=manor_ids)
        .order_by("manor_id", "id")
        .values(
            "id",
            "manor_id",
            "level",
            "template__key",
            "template__rarity",
            "template__archetype",
        )
    ):
        manor_id = int(guest_row["manor_id"])
        guest_id = int(guest_row["id"])
        guest_to_manor[guest_id] = manor_id
        records_by_manor[manor_id]["guests"].append(
            {
                "id": guest_id,
                "template": str(guest_row["template__key"]),
                "level": int(guest_row["level"] or 0),
                "rarity": str(guest_row["template__rarity"]),
                "archetype": str(guest_row["template__archetype"]),
            }
        )

    for gear_row in (
        GearItem.objects.filter(manor_id__in=manor_ids)
        .order_by("manor_id", "id")
        .values(
            "manor_id",
            "guest_id",
            "level",
            "template__key",
            "template__rarity",
            "template__slot",
        )
    ):
        records_by_manor[int(gear_row["manor_id"])]["gear"].append(
            {
                "guest_id": (int(gear_row["guest_id"]) if gear_row["guest_id"] is not None else None),
                "template": str(gear_row["template__key"]),
                "level": int(gear_row["level"] or 0),
                "rarity": str(gear_row["template__rarity"]),
                "slot": str(gear_row["template__slot"]),
            }
        )

    for skill_row in (
        GuestSkill.objects.filter(guest__manor_id__in=manor_ids)
        .order_by("guest__manor_id", "guest_id", "id")
        .values("guest_id", "skill__key", "skill__kind", "skill__rarity")
    ):
        skill_manor_id = guest_to_manor.get(int(skill_row["guest_id"]))
        if skill_manor_id is None:
            continue
        records_by_manor[skill_manor_id]["skills"].append(
            {
                "guest_id": int(skill_row["guest_id"]),
                "skill": str(skill_row["skill__key"]),
                "kind": str(skill_row["skill__kind"]),
                "rarity": str(skill_row["skill__rarity"]),
            }
        )

    for troop_row in (
        PlayerTroop.objects.filter(manor_id__in=manor_ids)
        .order_by("manor_id", "troop_template__key")
        .values("manor_id", "troop_template__key", "count")
    ):
        records_by_manor[int(troop_row["manor_id"])]["troops"][str(troop_row["troop_template__key"])] = int(
            troop_row["count"] or 0
        )

    for building_row in (
        Building.objects.filter(manor_id__in=manor_ids)
        .order_by("manor_id", "building_type__key")
        .values("manor_id", "building_type__key", "level")
    ):
        records_by_manor[int(building_row["manor_id"])]["buildings"][str(building_row["building_type__key"])] = int(
            building_row["level"] or 0
        )

    return [_finalize_profile_record(records_by_manor[manor_id]) for manor_id in manor_ids]


def _finalize_profile_record(record: dict[str, Any]) -> dict[str, Any]:
    guest_levels = [int(guest["level"]) for guest in record["guests"]]
    building_levels = [int(level) for level in record["buildings"].values()]
    troop_total = sum(max(0, int(count)) for count in record["troops"].values())
    guest_ids = [int(guest["id"]) for guest in record["guests"]]
    guest_ordinal = {guest_id: index for index, guest_id in enumerate(guest_ids)}

    roster_signature = [
        [guest["template"], guest["level"], guest["rarity"], guest["archetype"]] for guest in record["guests"]
    ]
    gear_signature = sorted(
        [
            guest_ordinal.get(gear["guest_id"], -1),
            gear["template"],
            gear["level"],
            gear["rarity"],
            gear["slot"],
        ]
        for gear in record["gear"]
    )
    skill_signature = sorted(
        [guest_ordinal.get(skill["guest_id"], -1), skill["skill"], skill["kind"], skill["rarity"]]
        for skill in record["skills"]
    )
    troop_signature = sorted([key, int(value)] for key, value in record["troops"].items())
    building_signature = sorted([key, int(value)] for key, value in record["buildings"].items())

    record["features"] = {
        "guest_count": len(guest_levels),
        "mean_guest_level": round(sum(guest_levels) / len(guest_levels), 6) if guest_levels else 0.0,
        "guest_level_gap": max(guest_levels) - min(guest_levels) if guest_levels else 0,
        "gear_count": len(record["gear"]),
        "skill_count": len(record["skills"]),
        "troop_total": troop_total,
        "troop_concentration": _troop_concentration(record["troops"]),
        "mean_building_level": (round(sum(building_levels) / len(building_levels), 6) if building_levels else 0.0),
    }
    record["signatures"] = {
        "roster": roster_signature,
        "gear": gear_signature,
        "skills": skill_signature,
        "troops": troop_signature,
        "buildings": building_signature,
        "joint": [roster_signature, gear_signature, skill_signature, troop_signature, building_signature],
    }
    return record


def _hard_constraint_violations(records: list[dict[str, Any]]) -> dict[str, int]:
    valid_rarities = {str(value) for value, _label in GuestRarity.choices}
    invalid_guest_levels = 0
    invalid_guest_rarities = 0
    duplicate_equipped_slots = 0
    skill_capacity_exceeded = 0
    max_skill_slots = int(GUEST.MAX_SKILL_SLOTS)

    for record in records:
        invalid_guest_levels += sum(int(guest["level"]) <= 0 for guest in record["guests"])
        invalid_guest_rarities += sum(str(guest["rarity"]) not in valid_rarities for guest in record["guests"])
        slots = Counter(
            (gear["guest_id"], str(gear["slot"])) for gear in record["gear"] if gear["guest_id"] is not None
        )
        duplicate_equipped_slots += sum(max(0, count - 1) for count in slots.values())
        skills_by_guest = Counter(int(skill["guest_id"]) for skill in record["skills"])
        skill_capacity_exceeded += sum(max(0, count - max_skill_slots) for count in skills_by_guest.values())

    return {
        "duplicate_equipped_slots": duplicate_equipped_slots,
        "invalid_guest_levels": invalid_guest_levels,
        "invalid_guest_rarities": invalid_guest_rarities,
        "skill_capacity_exceeded": skill_capacity_exceeded,
    }


def _cohort_summary(records: list[dict[str, Any]], *, include_bands: bool = True) -> dict[str, Any]:
    guests = [guest for record in records for guest in record["guests"]]
    gear = [item for record in records for item in record["gear"]]
    skills = [skill for record in records for skill in record["skills"]]
    features = [record["features"] for record in records]
    summary: dict[str, Any] = {
        "profile_count": len(records),
        "guest_count": len(guests),
        "profile_guest_count": _numeric_summary(feature["guest_count"] for feature in features),
        "guest_level": _numeric_summary(guest["level"] for guest in guests),
        "profile_guest_level_gap": _numeric_summary(feature["guest_level_gap"] for feature in features),
        "guest_rarity_ratio": _ratio_map(guest["rarity"] for guest in guests),
        "guest_archetype_ratio": _ratio_map(guest["archetype"] for guest in guests),
        "gear_per_profile": _numeric_summary(feature["gear_count"] for feature in features),
        "gear_rarity_ratio": _ratio_map(item["rarity"] for item in gear),
        "gear_slot_ratio": _ratio_map(item["slot"] for item in gear),
        "skills_per_profile": _numeric_summary(feature["skill_count"] for feature in features),
        "skill_kind_ratio": _ratio_map(skill["kind"] for skill in skills),
        "troop_total_per_profile": _numeric_summary(feature["troop_total"] for feature in features),
        "troop_concentration": _numeric_summary(feature["troop_concentration"] for feature in features),
        "mean_building_level": _numeric_summary(feature["mean_building_level"] for feature in features),
        "fingerprint_collision_rate": {
            name: _collision_rate(record["signatures"][name] for record in records)
            for name in ("roster", "gear", "skills", "troops", "buildings", "joint")
        },
        "robust_joint_outlier_rate": _robust_joint_outlier_rate(records),
        "hard_constraint_violations": _hard_constraint_violations(records),
    }
    if include_bands:
        bands = sorted({record["prestige_band"] for record in records})
        summary["by_prestige_band"] = {
            band: _cohort_summary(
                [record for record in records if record["prestige_band"] == band],
                include_bands=False,
            )
            for band in bands
        }
    return summary


def _sample_manors(*, sample_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    real_rows = [
        {
            "id": int(row["id"]),
            "region": str(row["region"]),
            "prestige": int(row["prestige"] or 0),
        }
        for row in Manor.objects.filter(
            bot_profile__isnull=True,
            guests__isnull=False,
        )
        .order_by("id")
        .values("id", "region", "prestige")
        .distinct()[:sample_limit]
    ]
    bot_rows = [
        {
            "id": int(row["manor_id"]),
            "region": str(row["manor__region"]),
            "prestige": int(row["manor__prestige"] or 0),
        }
        for row in BotProfile.objects.order_by("id").values(
            "manor_id",
            "manor__region",
            "manor__prestige",
        )[:sample_limit]
    ]
    return real_rows, bot_rows


def _profile_counts_by_band(
    records: list[dict[str, Any]],
    *,
    required_bands: tuple[str, ...],
) -> dict[str, int]:
    counts = Counter(str(record["prestige_band"]) for record in records)
    return {band: int(counts.get(band, 0)) for band in required_bands}


def _append_band_sample_blockers(
    blocking_reasons: list[str],
    *,
    cohort_label: str,
    counts: dict[str, int],
    required_minimum: int,
) -> None:
    for band, count in counts.items():
        if count < required_minimum:
            blocking_reasons.append(
                f"{cohort_label} prestige band {band!r} sample {count} " f"is below required minimum {required_minimum}"
            )


def _write_report_exclusive(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output_file:
        output_file.write(payload)


def build_virtual_player_baseline(
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    minimum_real_profiles: int = DEFAULT_MINIMUM_PROFILE_SAMPLE,
    minimum_bot_profiles: int = DEFAULT_MINIMUM_PROFILE_SAMPLE,
    minimum_profiles_per_band: int = DEFAULT_MINIMUM_PROFILES_PER_BAND,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    normalized_limit = int(sample_limit)
    if normalized_limit <= 0:
        raise ValueError("sample_limit must be positive")
    normalized_real_minimum = int(minimum_real_profiles)
    normalized_bot_minimum = int(minimum_bot_profiles)
    normalized_band_minimum = int(minimum_profiles_per_band)
    if normalized_real_minimum <= 0:
        raise ValueError("minimum_real_profiles must be positive")
    if normalized_bot_minimum <= 0:
        raise ValueError("minimum_bot_profiles must be positive")
    if normalized_band_minimum <= 0:
        raise ValueError("minimum_profiles_per_band must be positive")
    bands = virtual_player_prestige_bands()
    required_bands = tuple(sorted(str(name) for name in bands))
    if not required_bands:
        raise ValueError("virtual-player prestige bands must not be empty")

    query_counter = _QueryCounter()
    started_at = clock()
    with connection.execute_wrapper(query_counter):
        with _consistent_read_snapshot() as snapshot_contract:
            real_rows, bot_rows = _sample_manors(sample_limit=normalized_limit)
            real_records = _load_profile_records(real_rows, bands=bands)
            bot_records = _load_profile_records(bot_rows, bands=bands)
    duration_ms = round(max(0.0, clock() - started_at) * 1000, 3)

    real_band_counts = _profile_counts_by_band(real_records, required_bands=required_bands)
    bot_band_counts = _profile_counts_by_band(bot_records, required_bands=required_bands)
    blocking_reasons: list[str] = []
    if len(real_records) < normalized_real_minimum:
        blocking_reasons.append(
            f"real profile sample {len(real_records)} is below required minimum {normalized_real_minimum}"
        )
    if len(bot_records) < normalized_bot_minimum:
        blocking_reasons.append(
            f"V1 BotProfile sample {len(bot_records)} is below required minimum {normalized_bot_minimum}"
        )
    _append_band_sample_blockers(
        blocking_reasons,
        cohort_label="real profile",
        counts=real_band_counts,
        required_minimum=normalized_band_minimum,
    )
    _append_band_sample_blockers(
        blocking_reasons,
        cohort_label="V1 BotProfile",
        counts=bot_band_counts,
        required_minimum=normalized_band_minimum,
    )
    unclassified_real_count = sum(record["prestige_band"] == "unclassified" for record in real_records)
    unclassified_bot_count = sum(record["prestige_band"] == "unclassified" for record in bot_records)
    if unclassified_real_count:
        blocking_reasons.append(
            f"real profile sample contains {unclassified_real_count} unclassified prestige profiles"
        )
    if unclassified_bot_count:
        blocking_reasons.append(
            f"V1 BotProfile sample contains {unclassified_bot_count} unclassified prestige profiles"
        )

    stable_snapshot = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "sampling": {
            "bot_order": "BotProfile.id ASC",
            "minimum_bot_profiles": normalized_bot_minimum,
            "minimum_profiles_per_prestige_band": normalized_band_minimum,
            "minimum_real_profiles": normalized_real_minimum,
            "profile_counts_by_prestige_band": {
                "real": real_band_counts,
                "v1_bot": bot_band_counts,
            },
            "real_filter": "BotProfile absent and at least one Guest",
            "real_order": "Manor.id ASC",
            "required_prestige_bands": list(required_bands),
            "sample_limit_per_cohort": normalized_limit,
        },
        "cohorts": {
            "real": _cohort_summary(real_records),
            "v1_bot": _cohort_summary(bot_records),
        },
        "source_fingerprints": {
            "real": _checksum(real_records),
            "v1_bot": _checksum(bot_records),
        },
    }
    return {
        **stable_snapshot,
        "snapshot_checksum": _checksum(stable_snapshot),
        "status": "insufficient_samples" if blocking_reasons else "ready_for_threshold_review",
        "blocking_reasons": blocking_reasons,
        "audit_runtime": {
            "database_vendor": connection.vendor,
            "duration_ms": duration_ms,
            "query_count": query_counter.count,
            "snapshot_contract": snapshot_contract,
        },
        "maintenance_runtime": {
            "status": "not_measured_by_read_only_audit",
            "reason": "V1 maintenance timing and write-query counts require a disposable benchmark snapshot",
        },
    }


class Command(BaseCommand):
    help = "Generate a deterministic, read-only virtual-player Gate A baseline report."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
        parser.add_argument("--minimum-real-profiles", type=int, default=DEFAULT_MINIMUM_PROFILE_SAMPLE)
        parser.add_argument("--minimum-bot-profiles", type=int, default=DEFAULT_MINIMUM_PROFILE_SAMPLE)
        parser.add_argument(
            "--minimum-profiles-per-band",
            type=int,
            default=DEFAULT_MINIMUM_PROFILES_PER_BAND,
        )
        parser.add_argument("--output", type=str, default="")
        parser.add_argument("--fail-on-insufficient", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            report = build_virtual_player_baseline(
                sample_limit=int(options["sample_limit"]),
                minimum_real_profiles=int(options["minimum_real_profiles"]),
                minimum_bot_profiles=int(options["minimum_bot_profiles"]),
                minimum_profiles_per_band=int(options["minimum_profiles_per_band"]),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        payload = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        output_path = str(options.get("output") or "").strip()
        if output_path:
            path = Path(output_path)
            try:
                _write_report_exclusive(path, payload)
            except FileExistsError as exc:
                raise CommandError(f"refusing to overwrite existing report: {path}") from exc
            except OSError as exc:
                raise CommandError(f"failed to write baseline report {path}: {exc}") from exc
            self.stdout.write(str(path))
        else:
            self.stdout.write(payload, ending="")

        if bool(options.get("fail_on_insufficient")) and report["status"] != "ready_for_threshold_review":
            raise CommandError("virtual-player baseline has insufficient samples")
