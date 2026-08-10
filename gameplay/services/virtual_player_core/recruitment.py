"""Independent, durable recruitment for ordinary V2 virtual players.

The player-facing recruitment flow remains responsible for candidate rows and
action points.  This module owns the virtual-player branch: a bounded daily
plan, a frozen pool snapshot, normal silver spending, and automatic roster
settlement after the queue completes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Mapping, cast

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
    BotProfile,
    Manor,
    ResourceEvent,
    ResourceType,
)
from gameplay.services.resources import spend_resources_locked
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES
from guests.guest_upkeep_rules import get_guest_salary_for_rarity
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
from guests.services.recruitment_followups import schedule_guest_recruitment_completion
from guests.services.recruitment_guests import create_guest_from_template, grant_template_skills
from guests.services.recruitment_queries import get_pool_recruitment_duration_seconds
from guests.services.recruitment_shared import NON_REPEATABLE_RARITIES, invalidate_recruitment_hall_cache
from guests.services.recruitment_templates import (
    _get_hermit_templates,
    _get_recruitable_templates_by_rarity,
    choose_template_from_entries,
)
from guests.services.salary import bulk_check_salary_paid, get_guest_salary, quote_all_salaries
from guests.services.training import ensure_auto_training
from guests.utils.recruitment_utils import HERMIT_RARITY, get_recruitment_rarity_distribution

from .archetype_pacing import ArchetypePacing, resolve_archetype_pacing
from .config import load_virtual_player_config
from .maintenance_resources import salary_runway_commitment
from .projection import calculate_guest_arena_power
from .selectors import without_unresolved_external_reconciliations

logger = logging.getLogger(__name__)

VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION = 1
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
VIRTUAL_RECRUITMENT_RUNWAY_DAYS = 3


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


@dataclass(frozen=True, slots=True)
class VirtualRecruitmentResult:
    status: VirtualRecruitmentStatus
    reason: str
    recruitment_id: int | None = None
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class _DrawnGuest:
    guest: Guest
    rarity: str
    template_id: int


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


def _weighted_pool_plan(pacing: ArchetypePacing) -> tuple[tuple[str, int], ...]:
    """Build nine deterministic slots with weighted, bounded pool quotas."""

    weights = dict(pacing.recruitment_pool_weights)
    counts = {pool_key: 0 for pool_key in weights}
    plan: list[str] = []
    for _ in range(len(VIRTUAL_RECRUITMENT_POOL_PLAN)):
        selected_pool = max(
            weights,
            key=lambda pool_key: (
                weights[pool_key] / (counts[pool_key] + 1),
                -tuple(weights).index(pool_key),
            ),
        )
        counts[selected_pool] += 1
        plan.append(selected_pool)
    return tuple((pool_key, counts[pool_key]) for pool_key in plan)


def _operation_id(profile_id: int, quota_date: date, quota_ordinal: int, pacing: ArchetypePacing) -> str:
    pacing_digest = hashlib.sha256(
        json.dumps(pacing.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
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
    """Return the nine deterministic daily slots for one profile."""

    current_time = _normalize_now(now)
    quota_date = timezone.localdate(current_time)
    first_due = (
        _local_day_start(current_time)
        + VIRTUAL_RECRUITMENT_FIRST_SLOT
        + timedelta(seconds=_profile_stagger_seconds(int(profile_id), quota_date))
    )
    resolved_pacing = pacing or resolve_archetype_pacing(load_virtual_player_config(), "balanced")
    weighted_plan = _weighted_pool_plan(resolved_pacing)
    pool_quotas = dict(weighted_plan)
    return tuple(
        VirtualRecruitmentSchedule(
            profile_id=int(profile_id),
            quota_date=quota_date,
            quota_ordinal=ordinal,
            pool_key=pool_key,
            due_at=first_due + ordinal * VIRTUAL_RECRUITMENT_SLOT_INTERVAL,
            operation_id=_operation_id(int(profile_id), quota_date, ordinal, resolved_pacing),
            pool_quota=pool_quotas[pool_key],
        )
        for ordinal, (pool_key, _pool_quota) in enumerate(weighted_plan)
    )


def _normalize_distribution(
    distribution: list[tuple[str, int]] | tuple[tuple[str, int], ...]
) -> list[dict[str, int | str]]:
    normalized: list[dict[str, int | str]] = []
    total = 0
    for rarity, raw_weight in distribution:
        if isinstance(raw_weight, bool):
            raise VirtualRecruitmentError("recruitment rarity weight must be an integer")
        weight = int(raw_weight)
        if weight < 0:
            raise VirtualRecruitmentError("recruitment rarity weight must be non-negative")
        normalized.append({"rarity": str(rarity), "weight": weight})
        total += weight
    if total <= 0:
        raise VirtualRecruitmentError("recruitment rarity distribution must have positive weight")
    return normalized


def _strict_int(value: object, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VirtualRecruitmentError(f"virtual recruitment snapshot field {field} must be an integer")
    normalized = int(value)
    if minimum is not None and normalized < minimum:
        raise VirtualRecruitmentError(f"virtual recruitment snapshot field {field} is below its minimum")
    return normalized


def _build_pool_snapshot(*, pool: RecruitmentPool, manor: Manor, captured_at: datetime) -> dict[str, Any]:
    total_weight, _rarity_weights, rarity_distribution = get_recruitment_rarity_distribution()
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
            "total_weight": int(total_weight),
            "distribution": _normalize_distribution(rarity_distribution),
        },
        "entries": entries,
        "template_ids_by_rarity": template_ids_by_rarity,
    }


def _snapshot_pool(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_pool = snapshot.get("pool")
    if not isinstance(raw_pool, Mapping):
        raise VirtualRecruitmentError("virtual recruitment pool snapshot is missing")
    if (
        _strict_int(snapshot.get("snapshot_version"), field="snapshot_version", minimum=1)
        != VIRTUAL_RECRUITMENT_SNAPSHOT_VERSION
    ):
        raise VirtualRecruitmentError("unsupported virtual recruitment snapshot version")
    return raw_pool


def _snapshot_templates(snapshot: Mapping[str, Any]) -> tuple[dict[str, list[GuestTemplate]], dict[int, GuestTemplate]]:
    raw_by_rarity = snapshot.get("template_ids_by_rarity")
    if not isinstance(raw_by_rarity, Mapping):
        raise VirtualRecruitmentError("virtual recruitment snapshot has no template pool")
    ids_by_rarity: dict[str, list[int]] = {}
    all_ids: set[int] = set()
    for raw_rarity, raw_ids in raw_by_rarity.items():
        if not isinstance(raw_ids, (list, tuple)):
            raise VirtualRecruitmentError("virtual recruitment template ids must be lists")
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
        raise VirtualRecruitmentError("a frozen virtual recruitment template is no longer recruitable")
    templates_by_rarity = {
        rarity: [templates[template_id] for template_id in template_ids if template_id in templates]
        for rarity, template_ids in ids_by_rarity.items()
    }
    return templates_by_rarity, templates


def _snapshot_entries(snapshot: Mapping[str, Any], templates: Mapping[int, GuestTemplate]) -> list[SimpleNamespace]:
    raw_entries = snapshot.get("entries")
    if not isinstance(raw_entries, (list, tuple)):
        raise VirtualRecruitmentError("virtual recruitment pool entries are invalid")
    entries: list[SimpleNamespace] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise VirtualRecruitmentError("virtual recruitment pool entry is invalid")
        raw_template_id = raw_entry.get("template_id")
        template_id = (
            None if raw_template_id is None else _strict_int(raw_template_id, field="entry.template_id", minimum=1)
        )
        template = None if template_id is None else templates.get(template_id)
        if template_id is not None and template is None:
            raise VirtualRecruitmentError("virtual recruitment entry template is no longer recruitable")
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
        raise VirtualRecruitmentError("virtual recruitment rarity snapshot is missing")
    raw_total = raw_rarity.get("total_weight")
    distribution = raw_rarity.get("distribution")
    if isinstance(raw_total, bool) or not isinstance(distribution, (list, tuple)):
        raise VirtualRecruitmentError("virtual recruitment rarity snapshot is invalid")
    rows: list[tuple[str, int]] = []
    for row in distribution:
        if not isinstance(row, Mapping):
            raise VirtualRecruitmentError("virtual recruitment rarity row is invalid")
        rows.append(
            (
                str(row.get("rarity") or ""),
                _strict_int(row.get("weight"), field="rarity.weight", minimum=0),
            )
        )
    total = _strict_int(raw_total, field="rarity.total_weight", minimum=1)
    if total <= 0 or sum(weight for _rarity, weight in rows) != total:
        raise VirtualRecruitmentError("virtual recruitment rarity snapshot total is inconsistent")
    return str(cumulative_choice(rows, total, rng, default=GuestRarity.BLACK))


def _draw_virtual_candidates(
    *,
    manor: Manor,
    snapshot: Mapping[str, Any],
    seed: int,
) -> list[_DrawnGuest]:
    templates_by_rarity, templates = _snapshot_templates(snapshot)
    entries = _snapshot_entries(snapshot, templates)
    pool = _snapshot_pool(snapshot)
    raw_draw_count = pool.get("draw_count")
    draw_count = _strict_int(raw_draw_count, field="pool.draw_count", minimum=1)

    rng = random.Random(int(seed))
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
        custom_name = ""
        if template.rarity in (GuestRarity.BLACK, GuestRarity.GRAY) and not template.is_hermit:
            from guests.utils.name_generator import generate_random_name

            custom_name = generate_random_name(rng)
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


def _salary_runway_requirement(*, manor: Manor, guests: list[Guest], additional_salary: int, now: datetime) -> int:
    today = timezone.localdate(now)
    guest_ids = [int(guest.id) for guest in guests if getattr(guest, "id", None)]
    paid_today = bulk_check_salary_paid(guest_ids, today)
    current_quote = quote_all_salaries(manor, for_date=today, guests=guests, paid_guest_ids=paid_today)
    tomorrow_quote = quote_all_salaries(manor, for_date=today + timedelta(days=1), guests=guests, paid_guest_ids=set())
    daily_after_recruitment = int(tomorrow_quote.total_amount) + max(0, int(additional_salary))
    protected_future = daily_after_recruitment * VIRTUAL_RECRUITMENT_RUNWAY_DAYS
    protected_future += salary_runway_commitment(daily_after_recruitment) - daily_after_recruitment
    return int(current_quote.total_amount) + protected_future


def _max_snapshot_salary(snapshot: Mapping[str, Any]) -> int:
    raw_by_rarity = snapshot.get("template_ids_by_rarity")
    if not isinstance(raw_by_rarity, Mapping):
        raise VirtualRecruitmentError("virtual recruitment snapshot has no salary pool")
    rarities = [str(rarity) for rarity, ids in raw_by_rarity.items() if ids]
    if not rarities:
        raise NoTemplateAvailableError()
    return (
        max(get_guest_salary_for_rarity(rarity) for rarity in rarities if rarity != HERMIT_RARITY)
        if any(rarity != HERMIT_RARITY for rarity in rarities)
        else get_guest_salary_for_rarity(GuestRarity.BLACK)
    )


def _has_arena_history(guest_id: int) -> bool:
    return (
        ArenaEntryGuest.objects.filter(guest_id=int(guest_id)).exists()
        or ArenaCoopEntryGuest.objects.filter(guest_id=int(guest_id)).exists()
        or ArenaReserveTrainingAssignment.objects.filter(guest_id=int(guest_id)).exists()
    )


def _replacement_guest(*, guests: list[Guest], candidate: _DrawnGuest) -> Guest | None:
    candidate_power = _guest_power(candidate.guest)
    rarity_order = ("black", "gray", "green", "red", "blue", "purple", "orange")
    rarity_rank = {rarity: index for index, rarity in enumerate(rarity_order)}
    candidates = []
    for guest in guests:
        if (
            guest.status != GuestStatus.IDLE
            or guest.training_complete_at
            or guest.training_remaining_seconds is not None
        ):
            continue
        if _has_arena_history(int(guest.id)):
            continue
        guest_rank = rarity_rank.get(str(guest.rarity), -1)
        if rarity_rank.get(str(candidate.rarity), -1) <= guest_rank:
            continue
        guest_power = _guest_power(guest)
        if candidate_power <= guest_power:
            continue
        candidates.append((guest_rank, guest_power, int(guest.id), guest))
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row[0], row[1], row[2]))[-1]


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
) -> None:
    recruitment.status = GuestRecruitment.Status.COMPLETED
    recruitment.finished_at = current_time
    recruitment.result_count = int(result_count)
    recruitment.error_message = ""
    recruitment.save(update_fields=["status", "finished_at", "result_count", "error_message"])


def _eligible_profile_queryset(*, now: datetime):
    pending = GuestRecruitment.objects.filter(
        manor_id=OuterRef("manor_id"),
        status=GuestRecruitment.Status.PENDING,
    )
    reserved = ArenaVirtualReserveMember.objects.filter(profile_id=OuterRef("pk"))
    return without_unresolved_external_reconciliations(
        BotProfile.objects.filter(
            engine_version=2,
            policy_version=2,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        )
        .annotate(_has_pending_recruitment=Exists(pending), _has_arena_reserve=Exists(reserved))
        .filter(
            _has_pending_recruitment=False,
            _has_arena_reserve=False,
        )
        .filter(Q(next_recruitment_at__isnull=True) | Q(next_recruitment_at__lte=now))
    )


def _start_virtual_recruitment_locked(
    *,
    schedule: VirtualRecruitmentSchedule,
    now: datetime,
) -> VirtualRecruitmentResult:
    profile = (
        BotProfile.objects.select_for_update()
        .filter(
            pk=int(schedule.profile_id),
            engine_version=2,
            policy_version=2,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        )
        .first()
    )
    if profile is None or ArenaVirtualReserveMember.objects.filter(profile_id=profile.id).exists():
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_ELIGIBLE, "profile_not_eligible")

    manor = Manor.objects.select_for_update().get(pk=profile.manor_id)
    if GuestRecruitment.objects.filter(manor_id=manor.id, status=GuestRecruitment.Status.PENDING).exists():
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "recruitment_queue_busy")

    existing = GuestRecruitment.objects.filter(operation_id=schedule.operation_id).first()
    if existing is not None:
        return VirtualRecruitmentResult(
            VirtualRecruitmentStatus.ALREADY_EXISTS,
            "operation_already_exists",
            recruitment_id=int(existing.id),
            operation_id=schedule.operation_id,
        )

    pool = RecruitmentPool.objects.filter(key=schedule.pool_key).prefetch_related("entries__template").first()
    if pool is None:
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.MISSING_POOL, "pool_missing")

    quota_count = GuestRecruitment.objects.filter(
        bot_profile_id=profile.id,
        source=GuestRecruitment.Source.VIRTUAL,
        quota_date=schedule.quota_date,
        pool_id=pool.id,
    ).count()
    if quota_count >= int(schedule.pool_quota):
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "daily_quota_reached")

    locked_guests = list(
        Guest.objects.select_for_update().select_related("template").filter(manor_id=manor.id).order_by("id")
    )
    snapshot = _build_pool_snapshot(pool=pool, manor=manor, captured_at=now)
    pool_data = _snapshot_pool(snapshot)
    cost = dict(pool_data.get("cost") or {})
    if not cost or int(cost.get(str(ResourceType.SILVER), 0) or 0) <= 0:
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "recruitment_cost_missing")
    salary_commitment = _max_snapshot_salary(snapshot)
    silver_cost = int(cost.get(str(ResourceType.SILVER), 0) or 0)
    if int(manor.silver or 0) - silver_cost < _salary_runway_requirement(
        manor=manor,
        guests=locked_guests,
        additional_salary=salary_commitment,
        now=now,
    ):
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "salary_runway_protected")

    resolved_seed = resolve_recruitment_seed(
        int.from_bytes(
            hashlib.sha256(f"{profile.growth_seed}:{schedule.operation_id}".encode()).digest()[:8],
            "big",
        )
        % (2**31 - 1)
        + 1
    )
    duration_seconds = int(pool_data.get("duration_seconds") or 0)
    draw_count = int(pool_data.get("draw_count") or 0)
    if duration_seconds <= 0 or draw_count <= 0:
        raise VirtualRecruitmentError("virtual recruitment pool snapshot has invalid timing")

    try:
        with transaction.atomic():
            spend_resources_locked(
                manor,
                cost,
                note=f"虚拟玩家招募：{pool.name}",
                reason=ResourceEvent.Reason.RECRUIT_COST,
            )
            recruitment = GuestRecruitment.objects.create(
                manor=manor,
                bot_profile=profile,
                pool=pool,
                source=GuestRecruitment.Source.VIRTUAL,
                operation_id=schedule.operation_id,
                quota_date=schedule.quota_date,
                quota_ordinal=schedule.quota_ordinal,
                pool_snapshot=snapshot,
                salary_commitment=salary_commitment,
                cost=cost,
                draw_count=draw_count,
                duration_seconds=duration_seconds,
                seed=resolved_seed,
                complete_at=now + timedelta(seconds=duration_seconds),
            )
    except InsufficientResourceError:
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.DEFERRED, "insufficient_resource")

    schedule_guest_recruitment_completion(recruitment, duration_seconds, logger=logger)
    return VirtualRecruitmentResult(
        VirtualRecruitmentStatus.STARTED,
        "started",
        recruitment_id=int(recruitment.id),
        operation_id=schedule.operation_id,
    )


@transaction.atomic
def start_virtual_recruitment(
    schedule: VirtualRecruitmentSchedule,
    *,
    now: datetime | None = None,
) -> VirtualRecruitmentResult:
    current_time = _normalize_now(now)
    if schedule.due_at > current_time:
        return VirtualRecruitmentResult(
            VirtualRecruitmentStatus.NOT_DUE, "slot_not_due", operation_id=schedule.operation_id
        )
    try:
        return _start_virtual_recruitment_locked(schedule=schedule, now=current_time)
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
    schedules = _next_due_schedule(int(profile_id), now=current_time, pacing=pacing)
    if not schedules:
        return VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_DUE, "no_due_slot")
    last_result: VirtualRecruitmentResult | None = None
    for schedule in schedules:
        result = start_virtual_recruitment(schedule, now=current_time)
        if result.status in {
            VirtualRecruitmentStatus.STARTED,
            VirtualRecruitmentStatus.ALREADY_EXISTS,
        }:
            return result
        last_result = result
    return last_result or VirtualRecruitmentResult(VirtualRecruitmentStatus.NOT_DUE, "no_due_slot")


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
            },
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
            _defer_completion_locked(recruitment, current_time=current_time, reason="guest_capacity_full")
            return False

        effective_guests = [guest for guest in locked_guests if victim is None or guest.id != victim.id]
        if int(manor.silver or 0) < _salary_runway_requirement(
            manor=manor,
            guests=effective_guests,
            additional_salary=get_guest_salary(selected.guest),
            now=current_time,
        ):
            _defer_completion_locked(recruitment, current_time=current_time, reason="salary_runway_protected")
            return False

        if victim is not None:
            before_power = sum(_guest_power(guest) for guest in locked_guests)
            after_power = before_power - _guest_power(victim) + _guest_power(selected.guest)
            if after_power < before_power:
                _defer_completion_locked(recruitment, current_time=current_time, reason="roster_power_guard")
                return False
            victim.delete()

        selected.guest.save()
        grant_template_skills(selected.guest)
        RecruitmentRecord.objects.create(
            manor=manor,
            pool=recruitment.pool,
            guest=selected.guest,
            rarity=selected.rarity,
        )
        ensure_auto_training(selected.guest)
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
    "schedule_due_virtual_recruitments",
    "start_next_due_virtual_recruitment",
    "start_virtual_recruitment",
]
