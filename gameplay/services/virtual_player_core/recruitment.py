"""Independent, durable recruitment for ordinary V2 virtual players.

The player-facing recruitment flow remains responsible for candidate rows and
action points.  This module owns the virtual-player branch: a bounded daily
plan, a frozen pool snapshot, independent pool-cost spending, and immediate
roster settlement for one daily batch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Mapping, Sequence, cast

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from common.utils.random_utils import cumulative_choice
from core.exceptions import InsufficientResourceError, NoTemplateAvailableError, RecruitmentError
from gameplay.models import (
    ArenaCoopEntryGuest,
    ArenaEntryGuest,
    ArenaReserveTrainingAssignment,
    ArenaVirtualReserveMember,
    BotMaintenanceCompletionEvent,
    BotProfile,
    Manor,
    ResourceEvent,
    ResourceType,
)
from gameplay.services.resources import settle_resource_production_locked, spend_resources_locked
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES
from guests.models import (
    Guest,
    GuestRarity,
    GuestRecruitment,
    GuestStatus,
    GuestTemplate,
    RecruitmentPool,
    RecruitmentPoolEntry,
    RecruitmentRecord,
)
from guests.services.recruitment_candidates import resolve_candidate_draw_count
from guests.services.recruitment_flow import resolve_recruitment_cost, resolve_recruitment_seed
from guests.services.recruitment_guests import (
    build_recruitment_custom_name,
    create_guest_from_template,
    grant_template_skills,
)
from guests.services.recruitment_queries import get_pool_recruitment_duration_seconds
from guests.services.recruitment_shared import NON_REPEATABLE_RARITIES, invalidate_recruitment_hall_cache
from guests.services.recruitment_templates import (
    _get_hermit_templates,
    _get_recruitable_templates_by_rarity,
    choose_template_from_entries,
)
from guests.services.training import ensure_auto_training
from guests.utils.recruitment_utils import HERMIT_RARITY, get_recruitment_rarity_distribution

from .archetype_pacing import ArchetypePacing, resolve_archetype_pacing
from .config import load_virtual_player_config
from .projection import calculate_guest_arena_power
from .selectors import without_unresolved_external_reconciliations

logger = logging.getLogger(__name__)

VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION = 1
# Rarity policy is versioned independently from the pool snapshot contract.
# Existing queued rows without this field are legacy real-player distributions
# and must keep their already-frozen probabilities when they are finalized.
VIRTUAL_RECRUITMENT_RARITY_POLICY_LEGACY_VERSION = 1
VIRTUAL_RECRUITMENT_RARITY_POLICY_VERSION = 2
VIRTUAL_RECRUITMENT_RARITY_ADJUSTMENTS: tuple[tuple[str, int, int], ...] = (
    (GuestRarity.PURPLE.value, 1, 2),
    (GuestRarity.ORANGE.value, 1, 2),
)
VIRTUAL_RECRUITMENT_SCHEDULE_POLICY_VERSION = 2
VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD = 10_000
VIRTUAL_RECRUITMENT_SCAN_BATCH_SIZE = 200
VIRTUAL_RECRUITMENT_POOL_PLAN: tuple[str, ...] = (
    "dianshi",
    "xiangshi",
    "cunmu",
    "dianshi",
    "xiangshi",
    "cunmu",
    "dianshi",
    "xiangshi",
    "cunmu",
)
VIRTUAL_RECRUITMENT_LOCKED_POOL_PLAN: tuple[str, ...] = (
    "xiangshi",
    "cunmu",
    "xiangshi",
    "cunmu",
    "xiangshi",
    "cunmu",
)
VIRTUAL_RECRUITMENT_POOL_QUOTAS: Mapping[str, int] = {
    "dianshi": 3,
    "xiangshi": 3,
    "cunmu": 3,
}
VIRTUAL_RECRUITMENT_FIRST_SLOT = timedelta(minutes=30)
VIRTUAL_RECRUITMENT_SLOT_INTERVAL = timedelta(hours=2, minutes=30)
VIRTUAL_RECRUITMENT_PROFILE_STAGGER_SECONDS = 90 * 60
VIRTUAL_RECRUITMENT_COMPLETION_RETRY_DELAY = timedelta(minutes=15)
VIRTUAL_RECRUITMENT_SCAN_RETRY_DELAY = timedelta(minutes=15)


class VirtualRecruitmentStatus(StrEnum):
    STARTED = "started"
    ALREADY_EXISTS = "already_exists"
    NOT_DUE = "not_due"
    DEFERRED = "deferred"
    NOT_ELIGIBLE = "not_eligible"
    MISSING_POOL = "missing_pool"


@dataclass(frozen=True, slots=True)
class VirtualRecruitmentSchedule:
    profile_id: int
    quota_date: date
    quota_ordinal: int
    pool_key: str
    due_at: datetime
    operation_id: str
    pool_quota: int = 3
    dianshi_unlocked: bool = True


@dataclass(frozen=True, slots=True)
class VirtualRecruitmentResult:
    status: VirtualRecruitmentStatus
    reason: str
    recruitment_id: int | None = None
    operation_id: str | None = None
    recruitment_ids: tuple[int, ...] = ()
    deferred_slots: int = 0


@dataclass(frozen=True, slots=True)
class _DrawnGuest:
    guest: Guest
    rarity: str
    template_id: int


@dataclass(frozen=True, slots=True)
class _PreparedVirtualRecruitment:
    schedule: VirtualRecruitmentSchedule
    pool: RecruitmentPool
    snapshot: dict[str, Any]
    cost: dict[str, int]
    draw_count: int
    seed: int
    selected: _DrawnGuest
    salary_commitment: int


_SnapshotDrawContext = tuple[
    dict[str, list[GuestTemplate]],
    dict[int, GuestTemplate],
    list[SimpleNamespace],
]


class VirtualRecruitmentError(RecruitmentError):
    """A malformed virtual recruitment snapshot is a known domain failure."""


def _normalize_now(now: datetime | None) -> datetime:
    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        raise ValueError("virtual recruitment now must be timezone-aware")
    return current_time


def _local_day_start(current_time: datetime) -> datetime:
    local_time = timezone.localtime(current_time)
    return local_time.replace(hour=0, minute=0, second=0, microsecond=0)


def _profile_stagger_seconds(profile_id: int, quota_date: date) -> int:
    digest = hashlib.sha256(f"virtual-recruitment-stagger:{quota_date.isoformat()}:{int(profile_id)}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % VIRTUAL_RECRUITMENT_PROFILE_STAGGER_SECONDS


def _pool_plan(*, dianshi_unlocked: bool) -> tuple[str, ...]:
    """Return the fixed daily quota plan.

    Pool weights remain part of the archetype snapshot for future candidate
    quality policy, but daily pool counts are intentionally fixed: below the
    prestige threshold only the three 乡试 and three 村募 slots exist; at or
    above the threshold the three 殿选 slots are restored.
    """

    return VIRTUAL_RECRUITMENT_POOL_PLAN if dianshi_unlocked else VIRTUAL_RECRUITMENT_LOCKED_POOL_PLAN


def load_virtual_recruitment_pool_silver_costs() -> tuple[tuple[str, int], ...]:
    """Load the configured silver price of each ordinary virtual pool once.

    This is a read-only planning input.  Missing pools contribute no forecast
    because the recruitment writer cannot charge or settle a missing pool.
    The writer remains the sole owner of the real quote and deduction.
    """

    costs: dict[str, int] = {}
    for pool in RecruitmentPool.objects.filter(
        key__in={"dianshi", "xiangshi", "cunmu"},
    ).only("key", "cost"):
        try:
            cost = resolve_recruitment_cost(pool)
        except (AssertionError, RecruitmentError, TypeError, ValueError) as exc:
            raise VirtualRecruitmentError("虚拟招募池费用配置无效") from exc
        raw_silver = cost.get(str(ResourceType.SILVER), 0)
        if isinstance(raw_silver, bool) or not isinstance(raw_silver, int) or raw_silver < 0:
            raise VirtualRecruitmentError("虚拟招募池银两费用配置无效")
        costs[str(pool.key)] = int(raw_silver)
    return tuple(sorted(costs.items()))


def virtual_recruitment_daily_silver_cost(
    *,
    prestige: int,
    pool_silver_costs: Mapping[str, int] | tuple[tuple[str, int], ...],
) -> int:
    """Return the recurring daily silver outflow implied by recruitment.

    Below 10,000 prestige the plan contains only three 乡试 and three 村募
    slots.  At or above the threshold, three 殿选 slots are added.  This pure
    forecast never reserves or spends silver.
    """

    if isinstance(prestige, bool) or not isinstance(prestige, int) or prestige < 0:
        raise VirtualRecruitmentError("虚拟招募费用预测所需声望不能为负数")
    plan = _pool_plan(dianshi_unlocked=prestige >= VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD)
    return virtual_recruitment_daily_silver_cost_for_plan(
        pool_plan=plan,
        pool_silver_costs=pool_silver_costs,
    )


def _normalize_pool_silver_costs(
    pool_silver_costs: Mapping[str, int] | tuple[tuple[str, int], ...],
) -> dict[str, int]:
    rows = pool_silver_costs.items() if isinstance(pool_silver_costs, Mapping) else pool_silver_costs
    normalized: dict[str, int] = {}
    for raw_key, raw_cost in rows:
        key = str(raw_key).strip()
        if not key or key in normalized:
            raise VirtualRecruitmentError("虚拟招募费用预测中的招募池不能重复")
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, int) or raw_cost < 0:
            raise VirtualRecruitmentError("虚拟招募费用预测中的招募池费用必须是非负整数")
        normalized[key] = int(raw_cost)
    return normalized


def virtual_recruitment_daily_silver_cost_for_plan(
    *,
    pool_plan: Sequence[str],
    pool_silver_costs: Mapping[str, int] | tuple[tuple[str, int], ...],
) -> int:
    """Return the daily silver cost for an already-frozen pool plan."""

    if any(not isinstance(pool_key, str) or not pool_key.strip() for pool_key in pool_plan):
        raise VirtualRecruitmentError("虚拟招募费用预测中的招募池计划无效")
    normalized_plan = tuple(pool_key.strip() for pool_key in pool_plan)
    if any(pool_key not in {"dianshi", "xiangshi", "cunmu"} for pool_key in normalized_plan):
        raise VirtualRecruitmentError("虚拟招募费用预测中的招募池计划无效")
    normalized_costs = _normalize_pool_silver_costs(pool_silver_costs)
    return sum(int(normalized_costs.get(pool_key, 0)) for pool_key in normalized_plan)


def virtual_recruitment_daily_silver_cost_for_snapshot(
    *,
    snapshot: Mapping[str, Any],
    pool_silver_costs: Mapping[str, int] | tuple[tuple[str, int], ...],
) -> int:
    """Return the daily silver cost implied by a validated schedule snapshot."""

    raw_plan = snapshot.get("pool_plan")
    if not isinstance(raw_plan, (list, tuple)):
        raise VirtualRecruitmentError("虚拟招募配额快照中的招募池计划无效")
    return virtual_recruitment_daily_silver_cost_for_plan(
        pool_plan=raw_plan,
        pool_silver_costs=pool_silver_costs,
    )


def _pacing_digest(pacing: ArchetypePacing) -> str:
    return hashlib.sha256(
        json.dumps(pacing.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def _schedule_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    payload = {
        "policy_version": snapshot.get("policy_version"),
        "quota_date": snapshot.get("quota_date"),
        "prestige_threshold": snapshot.get("prestige_threshold"),
        "dianshi_unlocked": snapshot.get("dianshi_unlocked"),
        "pool_plan": snapshot.get("pool_plan"),
        "pacing_digest": snapshot.get("pacing_digest"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def _build_daily_schedule_snapshot(
    *,
    quota_date: date,
    prestige: int,
    pacing: ArchetypePacing,
) -> dict[str, Any]:
    dianshi_unlocked = int(prestige) >= VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD
    plan = _pool_plan(dianshi_unlocked=dianshi_unlocked)
    counts = {pool_key: plan.count(pool_key) for pool_key in ("dianshi", "xiangshi", "cunmu")}
    return {
        "policy_version": VIRTUAL_RECRUITMENT_SCHEDULE_POLICY_VERSION,
        "quota_date": quota_date.isoformat(),
        "prestige_threshold": VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
        "prestige_at_snapshot": max(0, int(prestige)),
        "dianshi_unlocked": dianshi_unlocked,
        "pool_plan": list(plan),
        "pool_quotas": counts,
        "pacing_digest": _pacing_digest(pacing),
    }


def _valid_daily_schedule_snapshot(
    value: object,
    *,
    quota_date: date,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("policy_version") != VIRTUAL_RECRUITMENT_SCHEDULE_POLICY_VERSION:
        return None
    if value.get("quota_date") != quota_date.isoformat():
        return None
    if value.get("prestige_threshold") != VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD:
        return None
    raw_snapshot_prestige = value.get("prestige_at_snapshot")
    if (
        isinstance(raw_snapshot_prestige, bool)
        or not isinstance(raw_snapshot_prestige, int)
        or raw_snapshot_prestige < 0
    ):
        return None
    if type(value.get("dianshi_unlocked")) is not bool:
        return None
    pacing_digest = value.get("pacing_digest")
    if (
        not isinstance(pacing_digest, str)
        or len(pacing_digest) != 12
        or any(character not in "0123456789abcdef" for character in pacing_digest.lower())
    ):
        return None
    if value["dianshi_unlocked"] != (raw_snapshot_prestige >= VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD):
        return None
    raw_plan = value.get("pool_plan")
    raw_quotas = value.get("pool_quotas")
    if not isinstance(raw_plan, (list, tuple)) or not isinstance(raw_quotas, Mapping):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in raw_plan):
        return None
    plan = tuple(item.strip() for item in raw_plan)
    if any(pool_key not in {"dianshi", "xiangshi", "cunmu"} for pool_key in plan):
        return None
    if value["dianshi_unlocked"]:
        expected_plan = VIRTUAL_RECRUITMENT_POOL_PLAN
    else:
        expected_plan = VIRTUAL_RECRUITMENT_LOCKED_POOL_PLAN
    if plan != expected_plan:
        return None
    expected_quotas = {pool_key: plan.count(pool_key) for pool_key in ("dianshi", "xiangshi", "cunmu")}
    if set(raw_quotas) != set(expected_quotas):
        return None
    if any(
        isinstance(raw_quotas.get(pool_key), bool) or not isinstance(raw_quotas.get(pool_key), int)
        for pool_key in expected_quotas
    ):
        return None
    quotas = {pool_key: int(raw_quotas[pool_key]) for pool_key in expected_quotas}
    if quotas != expected_quotas:
        return None
    return dict(value)


def _resolve_schedule_snapshot(
    *,
    profile: BotProfile | None,
    quota_date: date,
    prestige: int,
    pacing: ArchetypePacing,
) -> dict[str, Any]:
    if profile is not None:
        existing = _valid_daily_schedule_snapshot(profile.recruitment_schedule_snapshot, quota_date=quota_date)
        if existing is not None:
            return existing
    return _build_daily_schedule_snapshot(quota_date=quota_date, prestige=prestige, pacing=pacing)


def resolve_virtual_recruitment_schedule_snapshot(
    *,
    profile: BotProfile | None,
    quota_date: date,
    prestige: int,
    pacing: ArchetypePacing,
) -> dict[str, Any]:
    """Resolve the same frozen daily recruitment policy used by the writer."""

    return _resolve_schedule_snapshot(
        profile=profile,
        quota_date=quota_date,
        prestige=prestige,
        pacing=pacing,
    )


def _snapshot_from_schedule_rows(
    *,
    schedules: tuple[VirtualRecruitmentSchedule, ...],
    pacing: ArchetypePacing,
) -> dict[str, Any] | None:
    """Preserve a caller's already-materialized daily policy under the lock."""

    if not schedules:
        return None
    plan = tuple(schedule.pool_key for schedule in sorted(schedules, key=lambda row: row.quota_ordinal))
    unlocked = bool(schedules[0].dianshi_unlocked)
    expected_plan = _pool_plan(dianshi_unlocked=unlocked)
    if plan != expected_plan or any(bool(schedule.dianshi_unlocked) != unlocked for schedule in schedules):
        return None
    snapshot = _build_daily_schedule_snapshot(
        quota_date=schedules[0].quota_date,
        prestige=VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD if unlocked else VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD - 1,
        pacing=pacing,
    )
    return snapshot


def _schedule_rows_from_snapshot(
    *,
    profile_id: int,
    now: datetime,
    pacing: ArchetypePacing,
    snapshot: Mapping[str, Any],
) -> tuple[VirtualRecruitmentSchedule, ...]:
    quota_date = date.fromisoformat(str(snapshot["quota_date"]))
    first_due = (
        _local_day_start(now)
        + VIRTUAL_RECRUITMENT_FIRST_SLOT
        + timedelta(seconds=_profile_stagger_seconds(int(profile_id), quota_date))
    )
    plan = tuple(str(item) for item in snapshot["pool_plan"])
    pool_quotas = {str(key): int(value) for key, value in dict(snapshot["pool_quotas"]).items()}
    schedule_digest = _schedule_snapshot_digest(snapshot)
    dianshi_unlocked = bool(snapshot["dianshi_unlocked"])
    return tuple(
        VirtualRecruitmentSchedule(
            profile_id=int(profile_id),
            quota_date=quota_date,
            quota_ordinal=ordinal,
            pool_key=pool_key,
            due_at=first_due + ordinal * VIRTUAL_RECRUITMENT_SLOT_INTERVAL,
            operation_id=_operation_id(
                int(profile_id),
                quota_date,
                ordinal,
                pacing,
                schedule_digest=schedule_digest,
            ),
            pool_quota=pool_quotas[pool_key],
            dianshi_unlocked=dianshi_unlocked,
        )
        for ordinal, pool_key in enumerate(plan)
    )


def _operation_id(
    profile_id: int,
    quota_date: date,
    quota_ordinal: int,
    pacing: ArchetypePacing,
    *,
    schedule_digest: str | None = None,
) -> str:
    pacing_digest = schedule_digest or _pacing_digest(pacing)
    return (
        f"vp-recruit-v{VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION}:{int(profile_id)}:"
        f"{quota_date:%Y%m%d}:{int(quota_ordinal)}:{pacing_digest}"
    )


def iter_virtual_recruitment_schedule(
    profile_id: int,
    *,
    now: datetime | None = None,
    pacing: ArchetypePacing | None = None,
) -> tuple[VirtualRecruitmentSchedule, ...]:
    """Return the deterministic daily slots for one profile.

    The schedule is read-only here.  The first real settlement persists the
    same snapshot under the profile lock, so crossing 10,000 prestige during
    a day cannot retroactively add the three 殿选 slots.
    """

    current_time = _normalize_now(now)
    quota_date = timezone.localdate(current_time)
    profile = (
        BotProfile.objects.select_related("manor").filter(pk=int(profile_id)).first() if int(profile_id) > 0 else None
    )
    resolved_pacing = pacing or resolve_archetype_pacing(
        load_virtual_player_config(),
        str(profile.archetype) if profile is not None else "balanced",
    )
    snapshot = _resolve_schedule_snapshot(
        profile=profile,
        quota_date=quota_date,
        prestige=int(profile.manor.prestige) if profile is not None else VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD,
        pacing=resolved_pacing,
    )
    return _schedule_rows_from_snapshot(
        profile_id=int(profile_id),
        now=current_time,
        pacing=resolved_pacing,
        snapshot=snapshot,
    )


def _normalize_distribution(
    distribution: list[tuple[str, int]] | tuple[tuple[str, int], ...]
) -> list[dict[str, int | str]]:
    normalized: list[dict[str, int | str]] = []
    total = 0
    for rarity, raw_weight in distribution:
        if isinstance(raw_weight, bool):
            raise VirtualRecruitmentError("招募稀有度权重必须是整数")
        weight = int(raw_weight)
        if weight < 0:
            raise VirtualRecruitmentError("招募稀有度权重不能为负数")
        normalized.append({"rarity": str(rarity), "weight": weight})
        total += weight
    if total <= 0:
        raise VirtualRecruitmentError("招募稀有度分布必须包含正权重")
    return normalized


def _virtual_recruitment_rarity_adjustments_payload() -> dict[str, dict[str, int]]:
    return {
        rarity: {"numerator": int(numerator), "denominator": int(denominator)}
        for rarity, numerator, denominator in VIRTUAL_RECRUITMENT_RARITY_ADJUSTMENTS
    }


def _virtual_recruitment_rarity_distribution() -> tuple[int, tuple[tuple[str, int], ...]]:
    """Return the virtual-only rarity distribution without mutating global config.

    The real-player weights are the source of truth.  Halving purple/orange
    removes exactly 1,000 units from the configured million-unit range; the
    remainder is assigned to ordinary black rarity so the integer sampling
    range stays stable and the total probability remains exactly 100%.
    """

    total_weight, _rarity_weights, base_distribution = get_recruitment_rarity_distribution()
    normalized_base = tuple((str(rarity), int(weight)) for rarity, weight in base_distribution)
    if int(total_weight) <= 0 or sum(weight for _rarity, weight in normalized_base) != int(total_weight):
        raise VirtualRecruitmentError("真实玩家招募稀有度分布权重总数不一致")

    adjustments = {
        rarity: (int(numerator), int(denominator))
        for rarity, numerator, denominator in VIRTUAL_RECRUITMENT_RARITY_ADJUSTMENTS
    }
    adjusted: list[tuple[str, int]] = []
    black_index: int | None = None
    removed_weight = 0
    for index, (rarity, weight) in enumerate(normalized_base):
        if weight < 0:
            raise VirtualRecruitmentError("真实玩家招募稀有度权重不能为负数")
        if rarity == GuestRarity.BLACK.value:
            black_index = index
        factor = adjustments.get(rarity)
        if factor is None:
            adjusted_weight = weight
        else:
            numerator, denominator = factor
            if numerator < 0 or denominator <= 0:
                raise VirtualRecruitmentError("虚拟招募稀有度调整策略无效")
            scaled_weight = weight * numerator
            if scaled_weight % denominator != 0:
                raise VirtualRecruitmentError("虚拟招募稀有度权重无法按策略精确缩放")
            adjusted_weight = scaled_weight // denominator
            removed_weight += weight - adjusted_weight
        adjusted.append((rarity, adjusted_weight))

    if black_index is None:
        raise VirtualRecruitmentError("真实玩家招募稀有度分布缺少黑色稀有度")
    black_rarity, black_weight = adjusted[black_index]
    adjusted[black_index] = (black_rarity, black_weight + removed_weight)
    if sum(weight for _rarity, weight in adjusted) != int(total_weight):
        raise VirtualRecruitmentError("虚拟招募稀有度调整后权重总数不一致")
    return int(total_weight), tuple(adjusted)


def _strict_int(value: object, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VirtualRecruitmentError(f"虚拟招募快照中的{_snapshot_field_label(field)}必须是整数")
    normalized = int(value)
    if minimum is not None and normalized < minimum:
        raise VirtualRecruitmentError(f"虚拟招募快照中的{_snapshot_field_label(field)}低于允许的最小值")
    return normalized


def _snapshot_field_label(field: str) -> str:
    labels = {
        "snapshot_version": "版本",
        "template_id": "门客模板编号",
        "entry.template_id": "招募条目中的门客模板编号",
        "entry.id": "招募条目编号",
        "entry.weight": "招募条目权重",
        "rarity.weight": "稀有度权重",
        "rarity.total_weight": "稀有度权重总数",
        "rarity.policy_version": "稀有度策略版本",
    }
    return labels.get(str(field), "相关字段")


def _build_pool_snapshot(*, pool: RecruitmentPool, manor: Manor, captured_at: datetime) -> dict[str, Any]:
    total_weight, rarity_distribution = _virtual_recruitment_rarity_distribution()
    templates_by_rarity = _get_recruitable_templates_by_rarity()
    hermit_templates = _get_hermit_templates()
    template_ids_by_rarity = {
        str(rarity): [int(template.id) for template in templates]
        for rarity, templates in sorted(templates_by_rarity.items())
    }
    template_ids_by_rarity[HERMIT_RARITY] = [int(template.id) for template in hermit_templates]

    entries: list[dict[str, Any]] = []
    for entry in pool.entries.select_related("template").order_by("id"):
        entries.append(
            {
                "id": int(entry.id),
                "template_id": None if entry.template_id is None else int(entry.template_id),
                "rarity": None if entry.rarity is None else str(entry.rarity),
                "archetype": None if entry.archetype is None else str(entry.archetype),
                "weight": int(entry.weight),
            }
        )

    return {
        "snapshot_version": VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION,
        "captured_at": captured_at.astimezone(timezone.get_current_timezone()).isoformat(),
        "pool": {
            "id": int(pool.id),
            "key": str(pool.key),
            "name": str(pool.name),
            "tier": str(pool.tier),
            "cost": dict(resolve_recruitment_cost(pool)),
            "duration_seconds": int(get_pool_recruitment_duration_seconds(pool)),
            "draw_count": int(resolve_candidate_draw_count(pool=pool, manor=manor, total_draw_count=None)),
        },
        "rarity": {
            "policy_version": VIRTUAL_RECRUITMENT_RARITY_POLICY_VERSION,
            "source": "real_player_default",
            "adjustments": _virtual_recruitment_rarity_adjustments_payload(),
            "total_weight": int(total_weight),
            "distribution": _normalize_distribution(rarity_distribution),
        },
        "entries": entries,
        "template_ids_by_rarity": template_ids_by_rarity,
    }


def _snapshot_pool(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_pool = snapshot.get("pool")
    if not isinstance(raw_pool, Mapping):
        raise VirtualRecruitmentError("虚拟招募池快照缺失")
    if (
        _strict_int(snapshot.get("snapshot_version"), field="snapshot_version", minimum=1)
        != VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION
    ):
        raise VirtualRecruitmentError("不支持的虚拟招募快照版本")
    return raw_pool


def _snapshot_templates(snapshot: Mapping[str, Any]) -> tuple[dict[str, list[GuestTemplate]], dict[int, GuestTemplate]]:
    raw_by_rarity = snapshot.get("template_ids_by_rarity")
    if not isinstance(raw_by_rarity, Mapping):
        raise VirtualRecruitmentError("虚拟招募快照缺少门客模板池")
    ids_by_rarity: dict[str, list[int]] = {}
    all_ids: set[int] = set()
    for raw_rarity, raw_ids in raw_by_rarity.items():
        if not isinstance(raw_ids, (list, tuple)):
            raise VirtualRecruitmentError("虚拟招募门客模板编号必须是列表")
        ids: list[int] = []
        for raw_id in raw_ids:
            template_id = _strict_int(raw_id, field="template_id", minimum=1)
            ids.append(template_id)
            all_ids.add(template_id)
        ids_by_rarity[str(raw_rarity)] = ids

    templates = {
        int(template.id): template
        for template in GuestTemplate.objects.filter(id__in=all_ids, recruitable=True).prefetch_related(
            "initial_skills"
        )
    }
    missing_ids = all_ids - set(templates)
    if missing_ids:
        raise VirtualRecruitmentError("冻结的虚拟招募门客模板已不可招募")
    templates_by_rarity = {
        rarity: [templates[template_id] for template_id in template_ids if template_id in templates]
        for rarity, template_ids in ids_by_rarity.items()
    }
    return templates_by_rarity, templates


def _snapshot_entries(snapshot: Mapping[str, Any], templates: Mapping[int, GuestTemplate]) -> list[SimpleNamespace]:
    raw_entries = snapshot.get("entries")
    if not isinstance(raw_entries, (list, tuple)):
        raise VirtualRecruitmentError("虚拟招募池条目无效")
    entries: list[SimpleNamespace] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise VirtualRecruitmentError("虚拟招募池条目格式无效")
        raw_template_id = raw_entry.get("template_id")
        template_id = (
            None if raw_template_id is None else _strict_int(raw_template_id, field="entry.template_id", minimum=1)
        )
        template = None if template_id is None else templates.get(template_id)
        if template_id is not None and template is None:
            raise VirtualRecruitmentError("虚拟招募池条目中的门客模板已不可招募")
        entries.append(
            SimpleNamespace(
                id=_strict_int(raw_entry.get("id"), field="entry.id", minimum=1),
                template_id=template_id,
                template=template,
                rarity=raw_entry.get("rarity"),
                archetype=raw_entry.get("archetype"),
                weight=_strict_int(raw_entry.get("weight"), field="entry.weight", minimum=0),
            )
        )
    return entries


def _frozen_rarity(rng: random.Random, snapshot: Mapping[str, Any]) -> str:
    raw_rarity = snapshot.get("rarity")
    if not isinstance(raw_rarity, Mapping):
        raise VirtualRecruitmentError("虚拟招募稀有度快照缺失")
    raw_total = raw_rarity.get("total_weight")
    distribution = raw_rarity.get("distribution")
    if isinstance(raw_total, bool) or not isinstance(distribution, (list, tuple)):
        raise VirtualRecruitmentError("虚拟招募稀有度快照无效")
    policy_version = _strict_int(
        raw_rarity.get("policy_version", VIRTUAL_RECRUITMENT_RARITY_POLICY_LEGACY_VERSION),
        field="rarity.policy_version",
        minimum=1,
    )
    if policy_version not in {
        VIRTUAL_RECRUITMENT_RARITY_POLICY_LEGACY_VERSION,
        VIRTUAL_RECRUITMENT_RARITY_POLICY_VERSION,
    }:
        raise VirtualRecruitmentError("不支持的虚拟招募稀有度策略版本")
    if policy_version == VIRTUAL_RECRUITMENT_RARITY_POLICY_VERSION:
        if raw_rarity.get("adjustments") != _virtual_recruitment_rarity_adjustments_payload():
            raise VirtualRecruitmentError("虚拟招募稀有度策略元数据不一致")
    rows: list[tuple[str, int]] = []
    for row in distribution:
        if not isinstance(row, Mapping):
            raise VirtualRecruitmentError("虚拟招募稀有度记录无效")
        rows.append(
            (
                str(row.get("rarity") or ""),
                _strict_int(row.get("weight"), field="rarity.weight", minimum=0),
            )
        )
    total = _strict_int(raw_total, field="rarity.total_weight", minimum=1)
    if total <= 0 or sum(weight for _rarity, weight in rows) != total:
        raise VirtualRecruitmentError("虚拟招募稀有度快照权重总数不一致")
    return str(cumulative_choice(rows, total, rng, default=GuestRarity.BLACK))


def _draw_virtual_candidates(
    *,
    manor: Manor,
    snapshot: Mapping[str, Any],
    seed: int,
    excluded_template_ids: set[int] | None = None,
    snapshot_draw_context: _SnapshotDrawContext | None = None,
) -> list[_DrawnGuest]:
    if snapshot_draw_context is None:
        templates_by_rarity, templates = _snapshot_templates(snapshot)
        entries = _snapshot_entries(snapshot, templates)
    else:
        templates_by_rarity, templates, entries = snapshot_draw_context
    pool = _snapshot_pool(snapshot)
    raw_draw_count = pool.get("draw_count")
    draw_count = _strict_int(raw_draw_count, field="pool.draw_count", minimum=1)

    rng = random.Random(int(seed))
    excluded_ids = excluded_template_ids
    if excluded_ids is None:
        excluded_ids = {
            int(template_id)
            for template_id in Guest.objects.filter(manor_id=manor.id)
            .filter(template__rarity__in=NON_REPEATABLE_RARITIES)
            .values_list("template_id", flat=True)
        }
        excluded_ids.update(
            int(template_id)
            for template_id in Guest.objects.filter(
                manor_id=manor.id, template__rarity=GuestRarity.BLACK, template__is_hermit=True
            ).values_list("template_id", flat=True)
        )

    drawn: list[_DrawnGuest] = []
    for _index in range(draw_count):
        rarity = _frozen_rarity(rng, snapshot)
        template = choose_template_from_entries(
            cast(list[RecruitmentPoolEntry], entries),
            rng=rng,
            excluded_ids=excluded_ids,
            templates_by_rarity=templates_by_rarity,
            hermit_templates=templates_by_rarity.get(HERMIT_RARITY, []),
            rarity=rarity,
        )
        custom_name = build_recruitment_custom_name(template, rng)
        guest = create_guest_from_template(
            manor=manor,
            template=template,
            rarity=template.rarity,
            archetype=template.archetype,
            custom_name=custom_name,
            rng=rng,
            grant_skills=False,
            save=False,
        )
        drawn.append(_DrawnGuest(guest=guest, rarity=str(template.rarity), template_id=int(template.id)))
        if template.rarity in NON_REPEATABLE_RARITIES or (template.rarity == GuestRarity.BLACK and template.is_hermit):
            excluded_ids.add(int(template.id))
    return drawn


def _guest_power(guest: Guest) -> int:
    return calculate_guest_arena_power(
        force=int(guest.force),
        intellect=int(guest.intellect),
        defense=int(guest.defense_stat),
        agility=int(guest.agility),
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
    )


def _candidate_sort_key(candidate: _DrawnGuest) -> tuple[int, int, int]:
    rarity_rank = {
        rarity: index for index, rarity in enumerate(("black", "gray", "green", "red", "blue", "purple", "orange"))
    }.get(str(candidate.rarity), -1)
    return rarity_rank, _guest_power(candidate.guest), -int(candidate.template_id)


def _has_arena_history(guest_id: int) -> bool:
    return (
        ArenaEntryGuest.objects.filter(guest_id=int(guest_id)).exists()
        or ArenaCoopEntryGuest.objects.filter(guest_id=int(guest_id)).exists()
        or ArenaReserveTrainingAssignment.objects.filter(guest_id=int(guest_id)).exists()
    )


def _replacement_guest(*, guests: list[Guest], candidate: _DrawnGuest) -> Guest | None:
    rarity_order = ("black", "gray", "green", "red", "blue", "purple", "orange")
    rarity_rank = {rarity: index for index, rarity in enumerate(rarity_order)}
    candidates: list[tuple[int, int, Guest]] = []
    for guest in guests:
        if guest.status != GuestStatus.IDLE:
            continue
        if _has_arena_history(int(guest.id)):
            continue
        guest_rank = rarity_rank.get(str(guest.rarity), -1)
        if rarity_rank.get(str(candidate.rarity), -1) <= guest_rank:
            continue
        candidates.append((guest_rank, int(guest.id), guest))
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row[0], row[1]))[-1]


def _cancel_pending_guest_training_events(*, profile_id: int, guest_id: int) -> int:
    """Remove V2 completion inbox rows for a guest that is being replaced.

    The Celery timer is idempotent and will safely return ``not_found`` after
    the guest row is deleted.  The durable V2 inbox is separate from that
    timer, so it must be cleared explicitly to prevent a later reconciliation
    pass from treating the removed guest as an orphaned completion.  This
    path already holds ``BotProfile -> Manor``; lock the event rows last so
    completion reconciliation uses the same order.
    """

    event_ids = tuple(
        BotMaintenanceCompletionEvent.objects.select_for_update()
        .filter(
            profile_id=int(profile_id),
            domain_event_kind=BotMaintenanceCompletionEvent.DomainKind.GUEST_TRAINING,
            domain_object_id=int(guest_id),
            status=BotMaintenanceCompletionEvent.Status.PENDING,
        )
        .values_list("id", flat=True)
    )
    if not event_ids:
        return 0
    deleted, _details = BotMaintenanceCompletionEvent.objects.filter(pk__in=event_ids).delete()
    return int(deleted)


def _defer_completion_locked(recruitment: GuestRecruitment, *, current_time: datetime, reason: str) -> None:
    recruitment.complete_at = current_time + VIRTUAL_RECRUITMENT_COMPLETION_RETRY_DELAY
    recruitment.error_message = str(reason)[:255]
    recruitment.save(update_fields=["complete_at", "error_message"])
    if recruitment.bot_profile_id:
        BotProfile.objects.filter(pk=recruitment.bot_profile_id).update(
            next_recruitment_at=current_time + VIRTUAL_RECRUITMENT_COMPLETION_RETRY_DELAY,
            updated_at=current_time,
        )


def _mark_virtual_completion_locked(
    recruitment: GuestRecruitment,
    *,
    current_time: datetime,
    result_count: int,
    reason: str = "",
) -> None:
    recruitment.status = GuestRecruitment.Status.COMPLETED
    recruitment.finished_at = current_time
    recruitment.result_count = int(result_count)
    recruitment.error_message = str(reason)[:255]
    recruitment.save(update_fields=["status", "finished_at", "result_count", "error_message"])


def _eligible_profile_queryset(*, now: datetime):
    reserved = ArenaVirtualReserveMember.objects.filter(profile_id=OuterRef("pk"))
    return without_unresolved_external_reconciliations(
        BotProfile.objects.filter(
            engine_version=2,
            policy_version=2,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        )
        .annotate(_has_arena_reserve=Exists(reserved))
        .filter(_has_arena_reserve=False)
        .filter(Q(next_recruitment_at__isnull=True) | Q(next_recruitment_at__lte=now))
    )


def _current_non_repeatable_template_ids(*, manor_id: int) -> set[int]:
    excluded_ids = {
        int(template_id)
        for template_id in Guest.objects.filter(manor_id=manor_id)
        .filter(template__rarity__in=NON_REPEATABLE_RARITIES)
        .values_list("template_id", flat=True)
    }
    excluded_ids.update(
        int(template_id)
        for template_id in Guest.objects.filter(
            manor_id=manor_id,
            template__rarity=GuestRarity.BLACK,
            template__is_hermit=True,
        ).values_list("template_id", flat=True)
    )
    return excluded_ids


def _virtual_seed(*, profile: BotProfile, operation_id: str) -> int:
    return resolve_recruitment_seed(
        int.from_bytes(
            hashlib.sha256(f"{profile.growth_seed}:{operation_id}".encode()).digest()[:8],
            "big",
        )
        % (2**31 - 1)
        + 1
    )


def _spend_virtual_recruitment_cost_locked(
    *,
    manor: Manor,
    pool: RecruitmentPool,
    cost: dict[str, int],
) -> bool:
    """Spend one pool's cost without poisoning the surrounding batch transaction."""

    try:
        with transaction.atomic():
            spend_resources_locked(
                manor,
                cost,
                note=f"虚拟玩家招募：{pool.name}",
                reason=ResourceEvent.Reason.RECRUIT_COST,
                sync_production=False,
            )
    except InsufficientResourceError:
        # ``spend_resources_locked`` may settle offline production before the
        # balance check.  Restore the in-memory object after the savepoint
        # rolls back so later pool attempts see the same locked balance.
        manor.refresh_from_db()
        return False
    return True


def _create_virtual_recruitment_audit_locked(
    *,
    profile: BotProfile,
    manor: Manor,
    prepared: _PreparedVirtualRecruitment,
    now: datetime,
    result_count: int,
    reason: str = "",
) -> GuestRecruitment:
    snapshot = dict(prepared.snapshot)
    snapshot["settlement"] = {
        "mode": "instant_batch",
        "result_count": int(result_count),
        "reason": str(reason),
    }
    return GuestRecruitment.objects.create(
        manor=manor,
        bot_profile=profile,
        pool=prepared.pool,
        source=GuestRecruitment.Source.VIRTUAL,
        operation_id=prepared.schedule.operation_id,
        quota_date=prepared.schedule.quota_date,
        quota_ordinal=prepared.schedule.quota_ordinal,
        pool_snapshot=snapshot,
        salary_commitment=(int(prepared.salary_commitment) if int(result_count) > 0 else 0),
        cost=dict(prepared.cost),
        draw_count=int(prepared.draw_count),
        duration_seconds=0,
        seed=int(prepared.seed),
        status=GuestRecruitment.Status.COMPLETED,
        complete_at=now,
        finished_at=now,
        result_count=int(result_count),
        error_message=str(reason)[:255],
    )


def _create_virtual_capacity_terminal_audit_locked(
    *,
    profile: BotProfile,
    manor: Manor,
    schedule: VirtualRecruitmentSchedule,
    pool: RecruitmentPool | None,
    snapshot: Mapping[str, Any] | None,
    now: datetime,
    reason: str = "guest_capacity_full",
) -> GuestRecruitment:
    """Consume a skipped daily slot after a full roster has ended the batch."""

    terminal_snapshot = deepcopy(dict(snapshot)) if isinstance(snapshot, Mapping) else {}
    pool_data = terminal_snapshot.get("pool")
    if not isinstance(pool_data, Mapping):
        pool_data = {}
    raw_cost = pool_data.get("cost")
    cost = dict(raw_cost) if isinstance(raw_cost, Mapping) else {}
    raw_draw_count = pool_data.get("draw_count")
    draw_count = int(raw_draw_count) if isinstance(raw_draw_count, int) and not isinstance(raw_draw_count, bool) else 1
    if draw_count < 1:
        draw_count = 1
    terminal_snapshot["settlement"] = {
        "mode": "instant_batch",
        "result_count": 0,
        "reason": str(reason),
    }
    return GuestRecruitment.objects.create(
        manor=manor,
        bot_profile=profile,
        pool=pool,
        source=GuestRecruitment.Source.VIRTUAL,
        operation_id=schedule.operation_id,
        quota_date=schedule.quota_date,
        quota_ordinal=schedule.quota_ordinal,
        pool_snapshot=terminal_snapshot,
        salary_commitment=0,
        cost=cost,
        draw_count=draw_count,
        duration_seconds=0,
        seed=int(_virtual_seed(profile=profile, operation_id=schedule.operation_id)),
        status=GuestRecruitment.Status.COMPLETED,
        complete_at=now,
        finished_at=now,
        result_count=0,
        error_message=str(reason)[:255],
    )


def _start_virtual_recruitment_batch_locked(
    *,
    profile_id: int,
    schedules: tuple[VirtualRecruitmentSchedule, ...],
    now: datetime,
    settle_future_slots: bool = False,
    preferred_operation_id: str | None = None,
    pacing: ArchetypePacing | None = None,
) -> VirtualRecruitmentResult:
    """Settle all due slots in one locked, immediate virtual-player batch."""

    profile = (
        BotProfile.objects.select_for_update()
        .filter(
            pk=int(profile_id),
            engine_version=2,
            policy_version=2,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        )
        .first()
    )
    if profile is None or ArenaVirtualReserveMember.objects.filter(profile_id=profile.id).exists():
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_ELIGIBLE, "profile_not_eligible")

    manor = Manor.objects.select_for_update().get(pk=profile.manor_id)
    config = load_virtual_player_config()
    resolved_pacing = pacing or resolve_archetype_pacing(config, str(profile.archetype))
    quota_date = schedules[0].quota_date if schedules else timezone.localdate(now)
    schedule_snapshot = _valid_daily_schedule_snapshot(
        profile.recruitment_schedule_snapshot,
        quota_date=quota_date,
    )
    if schedule_snapshot is None:
        schedule_snapshot = _snapshot_from_schedule_rows(schedules=schedules, pacing=resolved_pacing)
        if schedule_snapshot is None:
            schedule_snapshot = _build_daily_schedule_snapshot(
                quota_date=quota_date,
                prestige=int(manor.prestige),
                pacing=resolved_pacing,
            )
        profile.recruitment_schedule_snapshot = schedule_snapshot
        profile.save(update_fields=["recruitment_schedule_snapshot", "updated_at"])
    frozen_schedules = _schedule_rows_from_snapshot(
        profile_id=int(profile.id),
        now=now,
        pacing=resolved_pacing,
        snapshot=schedule_snapshot,
    )
    due_schedules = (
        frozen_schedules
        if settle_future_slots
        else tuple(schedule for schedule in frozen_schedules if schedule.due_at <= now)
    )
    if not due_schedules:
        operation_id = schedules[0].operation_id if schedules else None
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_DUE, "no_due_slot", operation_id=operation_id)

    used_ordinals = set(
        GuestRecruitment.objects.filter(
            bot_profile_id=profile.id,
            source=GuestRecruitment.Source.VIRTUAL,
            quota_date=due_schedules[0].quota_date,
        ).values_list("quota_ordinal", flat=True)
    )
    pending_schedules = tuple(schedule for schedule in due_schedules if schedule.quota_ordinal not in used_ordinals)
    if not pending_schedules:
        existing = GuestRecruitment.objects.filter(operation_id=due_schedules[0].operation_id).first()
        if existing is not None:
            return VirtualRecruitmentResult(
                VirtualRecruitmentStatus.ALREADY_EXISTS,
                "operation_already_exists",
                recruitment_id=int(existing.id),
                operation_id=due_schedules[0].operation_id,
                recruitment_ids=(int(existing.id),),
            )
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_DUE, "no_unconsumed_due_slot")

    locked_guests = list(
        Guest.objects.select_for_update().select_related("template").filter(manor_id=manor.id).order_by("id")
    )
    excluded_ids = _current_non_repeatable_template_ids(manor_id=int(manor.id))
    pools_by_key: dict[str, RecruitmentPool | None] = {}
    snapshots_by_key: dict[str, dict[str, Any]] = {}
    draw_context_by_key: dict[str, _SnapshotDrawContext] = {}
    prepared: list[_PreparedVirtualRecruitment] = []
    prepared_by_operation: dict[str, _PreparedVirtualRecruitment] = {}
    deferred_reasons: list[str] = []
    deferred_reasons_by_operation: dict[str, str] = {}

    def defer_slot(schedule: VirtualRecruitmentSchedule, reason: str) -> None:
        deferred_reasons.append(reason)
        deferred_reasons_by_operation.setdefault(schedule.operation_id, reason)

    settle_resource_production_locked(manor, now=now)

    for schedule in pending_schedules:
        pool = pools_by_key.get(schedule.pool_key)
        if schedule.pool_key not in pools_by_key:
            pool = RecruitmentPool.objects.filter(key=schedule.pool_key).prefetch_related("entries__template").first()
            pools_by_key[schedule.pool_key] = pool
        if pool is None:
            defer_slot(schedule, "pool_missing")
            continue

        base_snapshot = snapshots_by_key.get(schedule.pool_key)
        if base_snapshot is None:
            base_snapshot = _build_pool_snapshot(pool=pool, manor=manor, captured_at=now)
            snapshots_by_key[schedule.pool_key] = base_snapshot
        snapshot = deepcopy(base_snapshot)
        draw_context = draw_context_by_key.get(schedule.pool_key)
        if draw_context is None:
            templates_by_rarity, templates = _snapshot_templates(snapshot)
            draw_context = (
                templates_by_rarity,
                templates,
                _snapshot_entries(snapshot, templates),
            )
            draw_context_by_key[schedule.pool_key] = draw_context
        pool_data = _snapshot_pool(snapshot)
        cost = dict(pool_data.get("cost") or {})
        if not cost or int(cost.get(str(ResourceType.SILVER), 0) or 0) <= 0:
            defer_slot(schedule, "recruitment_cost_missing")
            continue
        silver_cost = int(cost.get(str(ResourceType.SILVER), 0) or 0)
        if int(manor.silver or 0) < silver_cost:
            defer_slot(schedule, "insufficient_resource")
            continue
        draw_count = _strict_int(pool_data.get("draw_count"), field="pool.draw_count", minimum=1)
        resolved_seed = _virtual_seed(profile=profile, operation_id=schedule.operation_id)
        slot_excluded_ids = set(excluded_ids)
        try:
            drawn = _draw_virtual_candidates(
                manor=manor,
                snapshot=snapshot,
                seed=resolved_seed,
                excluded_template_ids=slot_excluded_ids,
                snapshot_draw_context=draw_context,
            )
        except (NoTemplateAvailableError, VirtualRecruitmentError):
            defer_slot(schedule, "no_virtual_candidate")
            continue
        if not drawn:
            defer_slot(schedule, "no_virtual_candidate")
            continue

        selected = max(drawn, key=_candidate_sort_key)
        if selected.rarity in NON_REPEATABLE_RARITIES or (
            selected.rarity == GuestRarity.BLACK and bool(selected.guest.template.is_hermit)
        ):
            # Reserve non-repeatable templates for this in-memory batch.  The
            # reservation is only used for drawing; the quota is consumed only
            # after the final roster and resource checks below pass.
            excluded_ids.add(int(selected.template_id))
        snapshot["candidate_preview"] = {
            "template_id": int(selected.template_id),
            "rarity": str(selected.rarity),
            "salary": 0,
            "custom_name": str(selected.guest.custom_name or selected.guest.template.name),
        }
        prepared_item = _PreparedVirtualRecruitment(
            schedule=schedule,
            pool=pool,
            snapshot=snapshot,
            cost=cost,
            draw_count=draw_count,
            seed=resolved_seed,
            selected=selected,
            salary_commitment=0,
        )
        prepared.append(prepared_item)
        prepared_by_operation[schedule.operation_id] = prepared_item

    if not prepared:
        reason = deferred_reasons[0] if deferred_reasons else "no_due_slot"
        status = (
            VirtualRecruitmentStatus.MISSING_POOL if reason == "pool_missing" else VirtualRecruitmentStatus.DEFERRED
        )
        operation_id = pending_schedules[0].operation_id if pending_schedules else None
        return VirtualRecruitmentResult(status, reason, operation_id=operation_id, deferred_slots=len(deferred_reasons))

    completed_ids_by_operation: dict[str, int] = {}
    accepted_any = False
    capacity_terminal = False
    for item in sorted(
        prepared,
        key=lambda row: (_candidate_sort_key(row.selected), -int(row.schedule.quota_ordinal)),
        reverse=True,
    ):
        selected = item.selected
        victim = _replacement_guest(guests=locked_guests, candidate=selected)
        result_count = 0
        reason = "guest_capacity_full" if victim is None and len(locked_guests) >= int(manor.guest_capacity) else ""
        if victim is not None:
            silver_cost = int(item.cost.get(str(ResourceType.SILVER), 0) or 0)
            if int(manor.silver or 0) < silver_cost:
                reason = "insufficient_resource"

        if reason:
            if reason == "guest_capacity_full":
                capacity_terminal = True
                break
            defer_slot(item.schedule, reason)
            continue

        # The pool slot is paid only after capacity and resource checks pass.
        # Replacement eligibility is deliberately rarity-only; combat power
        # and salary are not gates for virtual-player recruitment.
        if not _spend_virtual_recruitment_cost_locked(
            manor=manor,
            pool=item.pool,
            cost=item.cost,
        ):
            defer_slot(item.schedule, "insufficient_resource")
            continue

        if victim is not None:
            victim_id = int(victim.id)
            _cancel_pending_guest_training_events(profile_id=int(profile.id), guest_id=victim_id)
            locked_guests = [guest for guest in locked_guests if int(guest.id) != victim_id]
            victim.delete()
        selected.guest.save()
        grant_template_skills(selected.guest)
        RecruitmentRecord.objects.create(
            manor=manor,
            pool=item.pool,
            guest=selected.guest,
            rarity=selected.rarity,
        )
        ensure_auto_training(selected.guest, now=now)
        locked_guests.append(selected.guest)
        accepted_any = True
        result_count = 1

        audit = _create_virtual_recruitment_audit_locked(
            profile=profile,
            manor=manor,
            prepared=item,
            now=now,
            result_count=result_count,
            reason=reason,
        )
        completed_ids_by_operation[item.schedule.operation_id] = int(audit.id)

    batch_operation_id = (
        f"vp-recruit-batch-v{VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION}:{int(profile.id)}:"
        f"{pending_schedules[0].quota_date:%Y%m%d}"
    )
    if capacity_terminal:
        for schedule in pending_schedules:
            if schedule.operation_id in completed_ids_by_operation:
                continue
            terminal_reason = deferred_reasons_by_operation.get(schedule.operation_id, "guest_capacity_full")
            terminal_prepared_item = prepared_by_operation.get(schedule.operation_id)
            if terminal_prepared_item is not None:
                audit = _create_virtual_recruitment_audit_locked(
                    profile=profile,
                    manor=manor,
                    prepared=terminal_prepared_item,
                    now=now,
                    result_count=0,
                    reason=terminal_reason,
                )
            else:
                audit = _create_virtual_capacity_terminal_audit_locked(
                    profile=profile,
                    manor=manor,
                    schedule=schedule,
                    pool=pools_by_key.get(schedule.pool_key),
                    snapshot=snapshots_by_key.get(schedule.pool_key),
                    now=now,
                    reason=terminal_reason,
                )
            completed_ids_by_operation[schedule.operation_id] = int(audit.id)

        next_schedule = _next_unconsumed_schedule(
            int(profile.id),
            now=now,
            pacing=resolved_pacing,
        )
        profile.next_recruitment_at = None if next_schedule is None else next_schedule.due_at
        profile.save(update_fields=["next_recruitment_at", "updated_at"])
        if accepted_any:
            invalidate_recruitment_hall_cache(getattr(manor, "id", None))
        ordered_completed_ids = tuple(
            completed_ids_by_operation[schedule.operation_id]
            for schedule in pending_schedules
            if schedule.operation_id in completed_ids_by_operation
        )
        return VirtualRecruitmentResult(
            VirtualRecruitmentStatus.STARTED,
            "guest_capacity_full",
            recruitment_id=(
                completed_ids_by_operation.get(preferred_operation_id)
                if preferred_operation_id is not None
                else (ordered_completed_ids[0] if ordered_completed_ids else None)
            ),
            operation_id=preferred_operation_id or batch_operation_id,
            recruitment_ids=ordered_completed_ids,
        )

    if not accepted_any:
        reason = deferred_reasons[0] if deferred_reasons else "no_virtual_candidate"
        status = (
            VirtualRecruitmentStatus.MISSING_POOL if reason == "pool_missing" else VirtualRecruitmentStatus.DEFERRED
        )
        return VirtualRecruitmentResult(
            status,
            reason,
            operation_id=preferred_operation_id or batch_operation_id,
            deferred_slots=len(deferred_reasons),
        )

    if accepted_any:
        invalidate_recruitment_hall_cache(getattr(manor, "id", None))

    ordered_completed_ids = tuple(
        completed_ids_by_operation[schedule.operation_id]
        for schedule in pending_schedules
        if schedule.operation_id in completed_ids_by_operation
    )

    if preferred_operation_id is not None and preferred_operation_id not in completed_ids_by_operation:
        return VirtualRecruitmentResult(
            VirtualRecruitmentStatus.DEFERRED,
            deferred_reasons_by_operation.get(preferred_operation_id, "preferred_slot_deferred"),
            operation_id=preferred_operation_id,
            recruitment_ids=ordered_completed_ids,
            deferred_slots=len(deferred_reasons),
        )

    if deferred_reasons:
        deferred_recruitment_id = (
            completed_ids_by_operation.get(preferred_operation_id) if preferred_operation_id is not None else None
        )
        return VirtualRecruitmentResult(
            VirtualRecruitmentStatus.DEFERRED,
            deferred_reasons[0],
            recruitment_id=deferred_recruitment_id,
            operation_id=preferred_operation_id or batch_operation_id,
            recruitment_ids=ordered_completed_ids,
            deferred_slots=len(deferred_reasons),
        )

    return VirtualRecruitmentResult(
        VirtualRecruitmentStatus.STARTED,
        "batch_completed",
        recruitment_id=(
            completed_ids_by_operation.get(preferred_operation_id)
            if preferred_operation_id is not None
            else (ordered_completed_ids[0] if ordered_completed_ids else None)
        ),
        operation_id=preferred_operation_id or batch_operation_id,
        recruitment_ids=ordered_completed_ids,
        deferred_slots=len(deferred_reasons),
    )


@transaction.atomic
def start_virtual_recruitment(
    schedule: VirtualRecruitmentSchedule,
    *,
    now: datetime | None = None,
    pacing: ArchetypePacing | None = None,
) -> VirtualRecruitmentResult:
    current_time = _normalize_now(now)
    if schedule.due_at > current_time:
        return VirtualRecruitmentResult(
            VirtualRecruitmentStatus.NOT_DUE, "slot_not_due", operation_id=schedule.operation_id
        )
    try:
        existing = GuestRecruitment.objects.filter(operation_id=schedule.operation_id).first()
        if existing is not None:
            return VirtualRecruitmentResult(
                VirtualRecruitmentStatus.ALREADY_EXISTS,
                "operation_already_exists",
                recruitment_id=int(existing.id),
                operation_id=schedule.operation_id,
                recruitment_ids=(int(existing.id),),
            )
        daily_schedules = _next_daily_batch_schedule(
            int(schedule.profile_id),
            now=current_time,
            pacing=pacing,
            dianshi_unlocked_hint=schedule.dianshi_unlocked,
        )
        if not daily_schedules:
            daily_schedules = (schedule,)
        return _start_virtual_recruitment_batch_locked(
            profile_id=int(schedule.profile_id),
            schedules=daily_schedules,
            now=current_time,
            settle_future_slots=True,
            preferred_operation_id=schedule.operation_id,
            pacing=pacing,
        )
    except IntegrityError:
        logger.info("virtual recruitment start lost a uniqueness race", extra={"operation_id": schedule.operation_id})
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "uniqueness_race")


def _next_due_schedule(
    profile_id: int,
    *,
    now: datetime,
    pacing: ArchetypePacing | None = None,
) -> tuple[VirtualRecruitmentSchedule, ...]:
    schedules = iter_virtual_recruitment_schedule(profile_id, now=now, pacing=pacing)
    used_ordinals = set(
        GuestRecruitment.objects.filter(
            bot_profile_id=int(profile_id),
            source=GuestRecruitment.Source.VIRTUAL,
            quota_date=schedules[0].quota_date,
        ).values_list("quota_ordinal", flat=True)
    )
    return tuple(
        schedule for schedule in schedules if schedule.quota_ordinal not in used_ordinals and schedule.due_at <= now
    )


def _next_daily_batch_schedule(
    profile_id: int,
    *,
    now: datetime,
    pacing: ArchetypePacing | None = None,
    dianshi_unlocked_hint: bool | None = None,
) -> tuple[VirtualRecruitmentSchedule, ...]:
    """Return every unconsumed slot for today after the first slot is due."""

    schedules = iter_virtual_recruitment_schedule(profile_id, now=now, pacing=pacing)
    if (
        schedules
        and dianshi_unlocked_hint is not None
        and bool(schedules[0].dianshi_unlocked) != bool(dianshi_unlocked_hint)
    ):
        profile = BotProfile.objects.only("archetype").filter(pk=int(profile_id)).first()
        resolved_pacing = pacing or resolve_archetype_pacing(
            load_virtual_player_config(),
            str(getattr(profile, "archetype", "balanced")),
        )
        hinted_snapshot = _build_daily_schedule_snapshot(
            quota_date=schedules[0].quota_date,
            prestige=(
                VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD
                if dianshi_unlocked_hint
                else VIRTUAL_RECRUITMENT_PRESTIGE_THRESHOLD - 1
            ),
            pacing=resolved_pacing,
        )
        schedules = _schedule_rows_from_snapshot(
            profile_id=int(profile_id),
            now=now,
            pacing=resolved_pacing,
            snapshot=hinted_snapshot,
        )
    if not schedules or schedules[0].due_at > now:
        return ()
    used_ordinals = set(
        GuestRecruitment.objects.filter(
            bot_profile_id=int(profile_id),
            source=GuestRecruitment.Source.VIRTUAL,
            quota_date=schedules[0].quota_date,
        ).values_list("quota_ordinal", flat=True)
    )
    return tuple(schedule for schedule in schedules if schedule.quota_ordinal not in used_ordinals)


def _next_unconsumed_schedule(
    profile_id: int,
    *,
    now: datetime,
    pacing: ArchetypePacing,
) -> VirtualRecruitmentSchedule | None:
    """Return the earliest unconsumed slot across today and tomorrow.

    The durable profile hint is deliberately only a wake-up index.  The quota
    rows remain the source of truth, so a replay or a manual repair cannot make
    a slot disappear merely by changing the hint.
    """

    for day_offset in (0, 1):
        schedule_now = now + timedelta(days=day_offset)
        schedules = iter_virtual_recruitment_schedule(profile_id, now=schedule_now, pacing=pacing)
        if not schedules:
            continue
        used_ordinals = set(
            GuestRecruitment.objects.filter(
                bot_profile_id=int(profile_id),
                source=GuestRecruitment.Source.VIRTUAL,
                quota_date=schedules[0].quota_date,
            ).values_list("quota_ordinal", flat=True)
        )
        for schedule in schedules:
            if schedule.quota_ordinal not in used_ordinals:
                return schedule
    return None


def _refresh_recruitment_due_hint(
    profile_id: int,
    *,
    now: datetime,
    pacing: ArchetypePacing,
    retry: bool = False,
) -> None:
    """Update the indexed wake-up hint under the same profile lock boundary."""

    with transaction.atomic():
        profile = BotProfile.objects.select_for_update().filter(pk=int(profile_id)).first()
        if profile is None:
            return
        next_schedule = _next_unconsumed_schedule(int(profile.id), now=now, pacing=pacing)
        next_due = (
            now + VIRTUAL_RECRUITMENT_SCAN_RETRY_DELAY
            if retry
            else (None if next_schedule is None else next_schedule.due_at)
        )
        profile.next_recruitment_at = next_due
        profile.save(update_fields=["next_recruitment_at", "updated_at"])


def start_next_due_virtual_recruitment(
    profile_id: int,
    *,
    now: datetime | None = None,
    pacing: ArchetypePacing | None = None,
) -> VirtualRecruitmentResult:
    current_time = _normalize_now(now)
    schedules = _next_daily_batch_schedule(int(profile_id), now=current_time, pacing=pacing)
    if not schedules:
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_DUE, "no_due_slot")
    try:
        with transaction.atomic():
            return _start_virtual_recruitment_batch_locked(
                profile_id=int(profile_id),
                schedules=schedules,
                now=current_time,
                settle_future_slots=True,
                pacing=pacing,
            )
    except IntegrityError:
        logger.info(
            "virtual recruitment batch lost a uniqueness race",
            extra={"profile_id": int(profile_id)},
        )
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "uniqueness_race")


def schedule_due_virtual_recruitments(
    *, now: datetime | None = None, limit: int = VIRTUAL_RECRUITMENT_SCAN_BATCH_SIZE
) -> int:
    current_time = _normalize_now(now)
    normalized_limit = max(0, min(int(limit), VIRTUAL_RECRUITMENT_SCAN_BATCH_SIZE))
    if normalized_limit <= 0:
        return 0
    profiles = tuple(
        _eligible_profile_queryset(now=current_time)
        # A NULL hint means that the profile has not been indexed yet.  It is
        # intentionally initialized after known due rows so a large batch of
        # newly-created profiles cannot starve profiles whose slot is already
        # overdue.
        .order_by(F("next_recruitment_at").asc(nulls_last=True), "id").values("id", "archetype")[:normalized_limit]
    )
    started = 0
    config = load_virtual_player_config()
    for profile in profiles:
        pacing = resolve_archetype_pacing(config, str(profile["archetype"]))
        result = start_next_due_virtual_recruitment(int(profile["id"]), now=current_time, pacing=pacing)
        if result.status is VirtualRecruitmentStatus.STARTED:
            started += 1
        _refresh_recruitment_due_hint(
            int(profile["id"]),
            now=current_time,
            pacing=pacing,
            retry=result.status
            in {
                VirtualRecruitmentStatus.DEFERRED,
                VirtualRecruitmentStatus.NOT_ELIGIBLE,
                VirtualRecruitmentStatus.MISSING_POOL,
            }
            or result.reason == "batch_partial_deferred",
        )
    return started


def finalize_virtual_guest_recruitment(
    recruitment_id: int,
    *,
    now: datetime | None = None,
) -> bool:
    current_time = _normalize_now(now)
    source = GuestRecruitment.objects.filter(pk=int(recruitment_id)).values_list("source", flat=True).first()
    if source != GuestRecruitment.Source.VIRTUAL:
        return False

    with transaction.atomic():
        queue_identity = (
            GuestRecruitment.objects.filter(pk=int(recruitment_id)).values_list("bot_profile_id", flat=True).first()
        )
        if queue_identity is None:
            return False
        profile = BotProfile.objects.select_for_update().filter(pk=int(queue_identity)).first()
        if profile is None:
            return False
        manor = Manor.objects.select_for_update().get(pk=profile.manor_id)
        recruitment = (
            GuestRecruitment.objects.select_for_update()
            .select_related("pool")
            .filter(pk=int(recruitment_id), source=GuestRecruitment.Source.VIRTUAL)
            .first()
        )
        if recruitment is None or recruitment.status != GuestRecruitment.Status.PENDING:
            return False
        if recruitment.complete_at > current_time:
            return False

        try:
            drawn = _draw_virtual_candidates(
                manor=manor,
                snapshot=recruitment.pool_snapshot,
                seed=int(recruitment.seed),
            )
        except (NoTemplateAvailableError, VirtualRecruitmentError) as exc:
            recruitment.status = GuestRecruitment.Status.FAILED
            recruitment.finished_at = current_time
            recruitment.error_message = str(exc)[:255]
            recruitment.save(update_fields=["status", "finished_at", "error_message"])
            profile.next_recruitment_at = current_time + VIRTUAL_RECRUITMENT_SCAN_RETRY_DELAY
            profile.save(update_fields=["next_recruitment_at", "updated_at"])
            return False

        if not drawn:
            recruitment.status = GuestRecruitment.Status.FAILED
            recruitment.finished_at = current_time
            recruitment.error_message = "no_virtual_candidate"
            recruitment.save(update_fields=["status", "finished_at", "error_message"])
            profile.next_recruitment_at = current_time + VIRTUAL_RECRUITMENT_SCAN_RETRY_DELAY
            profile.save(update_fields=["next_recruitment_at", "updated_at"])
            return False

        selected = max(drawn, key=_candidate_sort_key)
        locked_guests = list(
            Guest.objects.select_for_update().select_related("template").filter(manor_id=manor.id).order_by("id")
        )
        victim = _replacement_guest(guests=locked_guests, candidate=selected)
        if victim is None and len(locked_guests) >= int(manor.guest_capacity):
            _mark_virtual_completion_locked(
                recruitment,
                current_time=current_time,
                result_count=0,
                reason="guest_capacity_full",
            )
            pacing = resolve_archetype_pacing(load_virtual_player_config(), str(profile.archetype))
            next_schedule = _next_unconsumed_schedule(int(profile.id), now=current_time, pacing=pacing)
            profile.next_recruitment_at = None if next_schedule is None else next_schedule.due_at
            profile.save(update_fields=["next_recruitment_at", "updated_at"])
            return True

        if victim is not None:
            _cancel_pending_guest_training_events(profile_id=int(profile.id), guest_id=int(victim.id))
            victim.delete()

        selected.guest.save()
        grant_template_skills(selected.guest)
        RecruitmentRecord.objects.create(
            manor=manor,
            pool=recruitment.pool,
            guest=selected.guest,
            rarity=selected.rarity,
        )
        ensure_auto_training(selected.guest, now=current_time)
        _mark_virtual_completion_locked(recruitment, current_time=current_time, result_count=1)
        pacing = resolve_archetype_pacing(load_virtual_player_config(), str(profile.archetype))
        next_schedule = _next_unconsumed_schedule(int(profile.id), now=current_time, pacing=pacing)
        profile.next_recruitment_at = None if next_schedule is None else next_schedule.due_at
        profile.save(update_fields=["next_recruitment_at", "updated_at"])
        invalidate_recruitment_hall_cache(getattr(manor, "id", None))
        return True


__all__ = [
    "VIRTUAL_RECRUITMENT_COMPLETION_RETRY_DELAY",
    "VIRTUAL_RECRUITMENT_FIRST_SLOT",
    "VIRTUAL_RECRUITMENT_POOL_PLAN",
    "VIRTUAL_RECRUITMENT_POOL_QUOTAS",
    "VIRTUAL_RECRUITMENT_SCAN_BATCH_SIZE",
    "VirtualRecruitmentError",
    "VirtualRecruitmentResult",
    "VirtualRecruitmentSchedule",
    "VirtualRecruitmentStatus",
    "finalize_virtual_guest_recruitment",
    "iter_virtual_recruitment_schedule",
    "load_virtual_recruitment_pool_silver_costs",
    "resolve_virtual_recruitment_schedule_snapshot",
    "schedule_due_virtual_recruitments",
    "start_next_due_virtual_recruitment",
    "start_virtual_recruitment",
    "virtual_recruitment_daily_silver_cost",
    "virtual_recruitment_daily_silver_cost_for_plan",
    "virtual_recruitment_daily_silver_cost_for_snapshot",
]
