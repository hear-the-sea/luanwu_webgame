"""Fixed-budget maintenance cycle state and durable audit adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from gameplay.models import BotMaintenanceAttempt, BotMaintenanceCycle, BotProfile

from .archetype_pacing import HIGH_COST_ACTION_KINDS


class MaintenanceCycleError(ValueError):
    pass


class CycleTrigger(StrEnum):
    SCHEDULED = "scheduled"
    ARENA_ACCELERATION = "arena_acceleration"


class MaintenanceReasonCategory(StrEnum):
    DOMAIN_CONSTRAINT = "domain_constraint"
    RESOURCE = "resource"
    SALARY = "salary"
    LOCK_CONFLICT = "lock_conflict"
    NO_CANDIDATE = "no_candidate"
    POLICY_GUARD = "policy_guard"
    RETRY_BACKOFF = "retry_backoff"
    OTHER = "other"


def classify_maintenance_reason(reason: str | None) -> MaintenanceReasonCategory:
    """Normalize maintenance outcomes into stable operational categories."""

    normalized = str(reason or "").strip()
    if normalized in {"candidate_domain_constraint", "domain_constraint"} or normalized.startswith(
        "archetype_parallel_training"
    ):
        return MaintenanceReasonCategory.DOMAIN_CONSTRAINT
    if normalized in {
        "insufficient_resource",
        "resource_snapshot_changed",
        "healing_insufficient_resource",
    } or normalized.startswith("archetype_budget_"):
        return MaintenanceReasonCategory.RESOURCE
    if normalized in {
        "salary_runway_protected",
        "salary_shortfall",
        "salary_no_action",
        "salary_already_paid",
    }:
        return MaintenanceReasonCategory.SALARY
    if normalized in {
        "profile_busy",
        "maintenance_sequence_conflict",
        "maintenance_identity_conflict",
        "maintenance_precondition_changed",
        "maintenance_plan_changed",
        "maintenance_target_changed",
        "maintenance_salary_changed",
    } or normalized.startswith("lock_"):
        return MaintenanceReasonCategory.LOCK_CONFLICT
    if normalized in {
        "no_candidate",
        "no_eligible_candidate",
        "arena_action_unavailable",
        "arena_no_action",
        "no_guests_to_heal",
        "candidate_exhausted",
    }:
        return MaintenanceReasonCategory.NO_CANDIDATE
    if normalized in {
        "strength_cap",
        "band_spacing",
        "band_action_cap",
        "multi_band_transition",
        "archetype_high_cost_cap",
        "arena_ineligible",
    }:
        return MaintenanceReasonCategory.POLICY_GUARD
    if normalized == "retry_backoff":
        return MaintenanceReasonCategory.RETRY_BACKOFF
    return MaintenanceReasonCategory.OTHER


ORDINARY_CYCLE_ACTION_SLOTS = 16
ARENA_CYCLE_ACTION_SLOTS = 8
ORDINARY_SLOT_INTERVAL_MINUTES_MIN = 10
ORDINARY_SLOT_INTERVAL_MINUTES_MAX = 15
ORDINARY_CYCLE_NEXT_START_MAX_DELAY = timedelta(hours=23)
NO_ACTION_CYCLE_KIND = "no_action"
ACTION_COMPLETION_SOURCE_MAINTENANCE_COMMIT = "maintenance_commit"
ACTION_COMPLETION_SOURCE_CANDIDATE_EXHAUSTED = "candidate_exhausted"
ORDINARY_ACTION_KINDS = (
    "building_upgrade",
    "technology_upgrade",
    "training",
    "equipment_equip",
    "skill_learning",
    "inventory_acquisition",
    "troop_recruitment",
    NO_ACTION_CYCLE_KIND,
)
ARENA_CYCLE_ACTION_KINDS = (
    "guest_recruitment",
    "training",
    "equipment_equip",
    "skill_learning",
    NO_ACTION_CYCLE_KIND,
)


@dataclass(frozen=True, slots=True)
class MaintenanceCycleState:
    cycle_id: str
    cycle_ordinal: int
    trigger: CycleTrigger
    max_actions: int
    action_ordinal: int = 0
    high_cost_actions_used: int = 0
    covered_action_kinds: tuple[str, ...] = ()
    used_business_keys: tuple[str, ...] = ()
    status: str = "open"

    def __post_init__(self) -> None:
        if not str(self.cycle_id).strip() or len(str(self.cycle_id).strip()) > 64:
            raise MaintenanceCycleError("cycle_id must be a non-empty value of at most 64 characters")
        if self.cycle_ordinal < 1 or self.max_actions < 1 or self.max_actions > ORDINARY_CYCLE_ACTION_SLOTS:
            raise MaintenanceCycleError("cycle ordinal and action limit are invalid")
        if self.action_ordinal < 0 or self.action_ordinal > self.max_actions:
            raise MaintenanceCycleError("action_ordinal exceeds the cycle budget")
        if self.high_cost_actions_used < 0 or self.high_cost_actions_used > self.action_ordinal:
            raise MaintenanceCycleError("high-cost action count exceeds committed action count")
        if self.trigger is CycleTrigger.ARENA_ACCELERATION and self.max_actions != ARENA_CYCLE_ACTION_SLOTS:
            raise MaintenanceCycleError("arena cycles require eight action slots")
        if self.trigger is CycleTrigger.SCHEDULED and self.max_actions != ORDINARY_CYCLE_ACTION_SLOTS:
            raise MaintenanceCycleError("ordinary cycles require sixteen action slots")
        covered = tuple(dict.fromkeys(str(value).strip() for value in self.covered_action_kinds if str(value).strip()))
        used = tuple(dict.fromkeys(str(value).strip() for value in self.used_business_keys if str(value).strip()))
        if len(covered) != len(self.covered_action_kinds) or len(used) != len(self.used_business_keys):
            raise MaintenanceCycleError("cycle audit keys must be unique and non-empty")
        object.__setattr__(self, "covered_action_kinds", covered)
        object.__setattr__(self, "used_business_keys", used)

    @property
    def exhausted(self) -> bool:
        return self.action_ordinal >= self.max_actions

    @property
    def completed(self) -> bool:
        return self.status == "completed"


def new_cycle_state(
    *,
    cycle_id: str,
    cycle_ordinal: int,
    trigger: CycleTrigger | str,
) -> MaintenanceCycleState:
    normalized_trigger = CycleTrigger(trigger)
    return MaintenanceCycleState(
        cycle_id=str(cycle_id),
        cycle_ordinal=int(cycle_ordinal),
        trigger=normalized_trigger,
        max_actions=(
            ARENA_CYCLE_ACTION_SLOTS
            if normalized_trigger is CycleTrigger.ARENA_ACCELERATION
            else ORDINARY_CYCLE_ACTION_SLOTS
        ),
    )


def allocate_cycle_action(
    state: MaintenanceCycleState,
    *,
    action_kind: str,
    business_key: str,
) -> tuple[MaintenanceCycleState, int]:
    if state.status != "open":
        raise MaintenanceCycleError("cannot allocate an action on a closed cycle")
    normalized_kind = str(action_kind).strip()
    normalized_key = str(business_key).strip()
    if not normalized_kind or not normalized_key:
        raise MaintenanceCycleError("action kind and business key are required")
    allowed = ARENA_CYCLE_ACTION_KINDS if state.trigger is CycleTrigger.ARENA_ACCELERATION else ORDINARY_ACTION_KINDS
    if normalized_kind not in allowed:
        raise MaintenanceCycleError(f"action kind {normalized_kind!r} is not allowed for this cycle")
    if normalized_key in state.used_business_keys:
        raise MaintenanceCycleError("business key has already been used in this cycle")
    if state.exhausted:
        raise MaintenanceCycleError("cycle action budget is exhausted")
    return (
        replace(
            state,
            action_ordinal=state.action_ordinal + 1,
            high_cost_actions_used=(
                state.high_cost_actions_used + (1 if normalized_kind in HIGH_COST_ACTION_KINDS else 0)
            ),
            covered_action_kinds=(
                state.covered_action_kinds
                if normalized_kind in state.covered_action_kinds
                else (*state.covered_action_kinds, normalized_kind)
            ),
            used_business_keys=(*state.used_business_keys, normalized_key),
        ),
        state.action_ordinal + 1,
    )


def finish_cycle(state: MaintenanceCycleState, *, reason: str = "") -> MaintenanceCycleState:
    del reason
    if state.status != "open":
        return state
    return replace(state, status="completed")


def cycle_action_limit(trigger: CycleTrigger | str) -> int:
    return (
        ARENA_CYCLE_ACTION_SLOTS
        if CycleTrigger(trigger) is CycleTrigger.ARENA_ACCELERATION
        else ORDINARY_CYCLE_ACTION_SLOTS
    )


def ordinary_slot_interval_minutes(
    cycle_id: str,
    slot_ordinal: int,
    *,
    minimum_minutes: int = ORDINARY_SLOT_INTERVAL_MINUTES_MIN,
    maximum_minutes: int = ORDINARY_SLOT_INTERVAL_MINUTES_MAX,
) -> int:
    """Return a stable bounded gap before one ordinary slot."""

    normalized_cycle_id = str(cycle_id).strip()
    normalized_slot_ordinal = int(slot_ordinal)
    if not normalized_cycle_id or len(normalized_cycle_id) > 64:
        raise MaintenanceCycleError("cycle_id must be a non-empty value of at most 64 characters")
    if normalized_slot_ordinal < 1 or normalized_slot_ordinal > ORDINARY_CYCLE_ACTION_SLOTS:
        raise MaintenanceCycleError("ordinary slot ordinal is outside the cycle budget")
    if (
        isinstance(minimum_minutes, bool)
        or isinstance(maximum_minutes, bool)
        or not isinstance(minimum_minutes, int)
        or not isinstance(maximum_minutes, int)
        or minimum_minutes < ORDINARY_SLOT_INTERVAL_MINUTES_MIN
        or maximum_minutes > ORDINARY_SLOT_INTERVAL_MINUTES_MAX
        or minimum_minutes > maximum_minutes
    ):
        raise MaintenanceCycleError("ordinary slot interval bounds must stay within 10..15 minutes")
    digest = sha256(f"{normalized_cycle_id}:slot:{normalized_slot_ordinal}".encode("utf-8")).digest()
    return minimum_minutes + (digest[0] % (maximum_minutes - minimum_minutes + 1))


def next_ordinary_slot_due_at(
    cycle_id: str,
    *,
    completed_at: datetime,
    next_slot_ordinal: int,
    interval_minutes: tuple[int, int] | None = None,
) -> datetime:
    """Calculate the next ordinary slot deadline without re-sampling."""

    if timezone.is_naive(completed_at):
        raise MaintenanceCycleError("completed_at must be timezone-aware")
    minimum_minutes, maximum_minutes = interval_minutes or (
        ORDINARY_SLOT_INTERVAL_MINUTES_MIN,
        ORDINARY_SLOT_INTERVAL_MINUTES_MAX,
    )
    return completed_at + timedelta(
        minutes=ordinary_slot_interval_minutes(
            cycle_id,
            next_slot_ordinal,
            minimum_minutes=minimum_minutes,
            maximum_minutes=maximum_minutes,
        ),
    )


def cycle_retry_due_at(cycle_id: str, *, now: datetime, reason: str) -> datetime:
    """Return a stable short backoff for a retry that must not consume a slot."""

    if timezone.is_naive(now):
        raise MaintenanceCycleError("now must be timezone-aware")
    normalized_cycle_id = str(cycle_id).strip()
    normalized_reason = str(reason).strip()
    if not normalized_cycle_id:
        raise MaintenanceCycleError("cycle_id must be non-empty")
    digest = sha256(f"{normalized_cycle_id}:retry:{normalized_reason}".encode("utf-8")).digest()
    return now + timedelta(minutes=1 + (digest[0] % 3))


def append_durable_cycle_action_locked(
    cycle: BotMaintenanceCycle,
    *,
    action_kind: str,
    business_key: str,
    reason: str = "",
    persist: bool = True,
) -> tuple[BotMaintenanceCycle, int]:
    """Allocate one slot on a cycle row already locked by the caller.

    ``persist=False`` is reserved for callers that already own the same
    transaction and will fold the allocation into a later cycle update.  The
    default keeps the standalone helper's durable behavior unchanged.
    """

    keys = [str(value) for value in (cycle.used_business_keys or [])]
    if str(business_key) in keys:
        raise MaintenanceCycleError("business key has already been used in this cycle")
    if int(cycle.action_ordinal) >= int(cycle.max_actions):
        raise MaintenanceCycleError("cycle action budget is exhausted")
    allowed = (
        ARENA_CYCLE_ACTION_KINDS
        if cycle.trigger == BotMaintenanceCycle.Trigger.ARENA_ACCELERATION
        else ORDINARY_ACTION_KINDS
    )
    if str(action_kind) not in allowed:
        raise MaintenanceCycleError("action kind is not allowed for this cycle")
    ordinal = int(cycle.action_ordinal) + 1
    cycle.action_ordinal = ordinal
    if str(action_kind) in HIGH_COST_ACTION_KINDS:
        cycle.high_cost_actions_used = int(cycle.high_cost_actions_used or 0) + 1
    covered_action_kinds = [str(value) for value in (cycle.covered_action_kinds or [])]
    if str(action_kind) not in covered_action_kinds:
        covered_action_kinds.append(str(action_kind))
    cycle.covered_action_kinds = covered_action_kinds
    cycle.used_business_keys = [*keys, str(business_key)]
    cycle.last_reason = str(reason or "")[:64]
    cycle.current_action_state = BotMaintenanceCycle.ActionState.SUBMITTED
    cycle.last_action_completion_source = ACTION_COMPLETION_SOURCE_MAINTENANCE_COMMIT
    if persist:
        cycle.save(
            update_fields=[
                "action_ordinal",
                "high_cost_actions_used",
                "covered_action_kinds",
                "used_business_keys",
                "last_reason",
                "current_action_state",
                "last_action_completion_source",
                "updated_at",
            ]
        )
    return cycle, ordinal


@transaction.atomic
def append_durable_cycle_action(
    cycle_id: str,
    *,
    action_kind: str,
    business_key: str,
    reason: str = "",
) -> tuple[BotMaintenanceCycle, int]:
    """Allocate and persist one cycle slot with a unique business identity."""

    cycle = BotMaintenanceCycle.objects.select_for_update().get(cycle_id=str(cycle_id))
    return append_durable_cycle_action_locked(
        cycle,
        action_kind=action_kind,
        business_key=business_key,
        reason=reason,
    )


def _metric_non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return int(value)


def _attempt_resource_cost(shadow_cost: Mapping[str, Any], resource: str) -> int:
    resource_costs = shadow_cost.get("resource_costs")
    if isinstance(resource_costs, Mapping):
        value = resource_costs.get(resource)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return int(value)
    for key in (f"real_{resource}", resource):
        value = shadow_cost.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return 0


def _attempt_action_kind(raw_attempt: Mapping[str, Any], shadow_cost: Mapping[str, Any]) -> str:
    action_kind = str(raw_attempt.get("action_kind") or "").strip()
    if action_kind:
        return action_kind[:32]
    inferred_action_kind = {
        "salary_batch": "salary_settlement",
        "salary_child": "salary_settlement",
        "guest_healing_sweep": "guest_healing",
        "guest_healing_child": "guest_healing",
        "inventory_draw": "inventory_acquisition",
    }.get(str(shadow_cost.get("kind") or ""), "")
    if inferred_action_kind:
        return inferred_action_kind
    if raw_attempt.get("outcome") in {BotMaintenanceAttempt.Outcome.NO_ACTION, "no_action"}:
        return NO_ACTION_CYCLE_KIND
    return ""


def _record_durable_attempts_locked(
    profile: BotProfile,
    *,
    attempts: Sequence[Mapping[str, Any]],
    return_objects: bool = True,
    assume_new: bool = False,
) -> tuple[BotMaintenanceAttempt, ...]:
    normalized_specs: list[dict[str, Any]] = []
    operation_ids: list[str] = []
    for raw_attempt in attempts:
        normalized_operation_id = str(raw_attempt.get("operation_id", "")).strip()
        if not normalized_operation_id:
            raise MaintenanceCycleError("attempt operation_id must be non-empty")
        if normalized_operation_id in operation_ids:
            raise MaintenanceCycleError("attempt operation_ids must be unique")
        operation_ids.append(normalized_operation_id)
        shadow_cost = dict(raw_attempt.get("shadow_cost") or {})
        normalized_reason = str(raw_attempt.get("reason", "") or "")[:64]
        reason_category = classify_maintenance_reason(normalized_reason).value if normalized_reason else ""
        normalized_specs.append(
            {
                "operation_id": normalized_operation_id,
                "cycle": raw_attempt.get("cycle"),
                "trigger": CycleTrigger(str(raw_attempt.get("trigger"))).value,
                "archetype": str(raw_attempt.get("archetype") or getattr(profile, "archetype", ""))[:16],
                "action_kind": _attempt_action_kind(raw_attempt, shadow_cost),
                "round_ordinal": raw_attempt.get("round_ordinal"),
                "action_ordinal_in_round": raw_attempt.get("action_ordinal_in_round"),
                "attempt_ordinal": int(raw_attempt.get("attempt_ordinal", 1)),
                "outcome": str(raw_attempt.get("outcome", "")),
                "reason": normalized_reason,
                "reason_category": reason_category,
                "dispatched": bool(raw_attempt.get("dispatched", False)),
                "receipt_operation_id": str(raw_attempt.get("receipt_operation_id", "") or "")[:64],
                "shadow_cost": shadow_cost,
                "silver_cost": _attempt_resource_cost(shadow_cost, "silver"),
                "grain_cost": _attempt_resource_cost(shadow_cost, "grain"),
                "salary_runway_days": min(
                    32,
                    _metric_non_negative_int(
                        raw_attempt.get("salary_runway_days", shadow_cost.get("salary_runway_days", 0))
                    ),
                ),
                "salary_runway_silver": _metric_non_negative_int(
                    raw_attempt.get("salary_runway_silver", shadow_cost.get("salary_runway_silver", 0))
                ),
                "started_at": raw_attempt.get("started_at") or timezone.now(),
            }
        )

    existing_by_operation_id: dict[str, BotMaintenanceAttempt] = {}
    if not assume_new:
        existing_by_operation_id = BotMaintenanceAttempt.objects.in_bulk(
            operation_ids,
            field_name="operation_id",
        )
        for operation_id, existing in existing_by_operation_id.items():
            spec = next(item for item in normalized_specs if item["operation_id"] == operation_id)
            if int(existing.profile_id) != int(profile.pk) or existing.outcome != spec["outcome"]:
                raise MaintenanceCycleError("attempt operation_id belongs to a different request")

    missing_specs = (
        normalized_specs
        if assume_new
        else [spec for spec in normalized_specs if spec["operation_id"] not in existing_by_operation_id]
    )
    if missing_specs:
        new_attempts = [
            BotMaintenanceAttempt(
                profile=profile,
                **spec,
            )
            for spec in missing_specs
        ]
        if assume_new:
            # The caller owns the profile/cycle lock and has allocated fresh
            # operation identities in the same transaction.  Keeping the
            # insert in that transaction removes an otherwise redundant
            # savepoint; the public idempotent path below retains the
            # collision recovery boundary.
            BotMaintenanceAttempt.objects.bulk_create(new_attempts)
        else:
            try:
                with transaction.atomic():
                    BotMaintenanceAttempt.objects.bulk_create(new_attempts)
            except IntegrityError:
                # A replay can race the first writer.  Re-read all identities and
                # keep the same payload/profile conflict checks as the single-row
                # path; unrelated database failures still propagate.
                existing_by_operation_id = BotMaintenanceAttempt.objects.in_bulk(
                    operation_ids,
                    field_name="operation_id",
                )
                if len(existing_by_operation_id) != len(operation_ids):
                    raise
                for operation_id, existing in existing_by_operation_id.items():
                    spec = next(item for item in normalized_specs if item["operation_id"] == operation_id)
                    if int(existing.profile_id) != int(profile.pk) or existing.outcome != spec["outcome"]:
                        raise MaintenanceCycleError("attempt operation_id belongs to a different request")

    if not return_objects:
        return ()
    all_by_operation_id = (
        existing_by_operation_id
        if not missing_specs
        else BotMaintenanceAttempt.objects.in_bulk(
            operation_ids,
            field_name="operation_id",
        )
    )
    if len(all_by_operation_id) != len(operation_ids):
        raise MaintenanceCycleError("attempt batch did not persist all operation identities")
    return tuple(all_by_operation_id[operation_id] for operation_id in operation_ids)


@transaction.atomic
def record_durable_attempts(
    profile: BotProfile,
    *,
    attempts: Sequence[Mapping[str, Any]],
    return_objects: bool = True,
) -> tuple[BotMaintenanceAttempt, ...]:
    """Persist a bounded idempotent attempt batch with one read and one write.

    Callers that only need durable persistence can disable the final identity
    read; the default keeps the object-returning API used by replay callers.
    """

    return _record_durable_attempts_locked(
        profile,
        attempts=attempts,
        return_objects=return_objects,
    )


def record_durable_attempts_locked(
    profile: BotProfile,
    *,
    attempts: Sequence[Mapping[str, Any]],
    return_objects: bool = True,
    assume_new: bool = False,
) -> tuple[BotMaintenanceAttempt, ...]:
    """Persist attempts inside a caller-owned transaction and lock boundary.

    ``assume_new`` is reserved for callers that allocated fresh operation IDs
    while holding the corresponding durable cycle lock.  The normal public
    entry point remains the replay-safe default.
    """

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("record_durable_attempts_locked requires an atomic transaction")
    return _record_durable_attempts_locked(
        profile,
        attempts=attempts,
        return_objects=return_objects,
        assume_new=assume_new,
    )


@transaction.atomic
def record_durable_attempt(
    profile: BotProfile,
    *,
    operation_id: str,
    trigger: CycleTrigger | str,
    attempt_ordinal: int,
    outcome: str,
    reason: str = "",
    archetype: str = "",
    action_kind: str = "",
    cycle: BotMaintenanceCycle | None = None,
    round_ordinal: int | None = None,
    action_ordinal_in_round: int | None = None,
    dispatched: bool = False,
    receipt_operation_id: str = "",
    shadow_cost: Mapping[str, Any] | None = None,
    salary_runway_days: int = 0,
    salary_runway_silver: int = 0,
    started_at: datetime | None = None,
) -> BotMaintenanceAttempt:
    """Write an immutable attempt; replaying the same operation is idempotent."""

    return record_durable_attempts(
        profile,
        attempts=(
            {
                "operation_id": operation_id,
                "cycle": cycle,
                "trigger": trigger,
                "archetype": archetype,
                "action_kind": action_kind,
                "round_ordinal": round_ordinal,
                "action_ordinal_in_round": action_ordinal_in_round,
                "attempt_ordinal": attempt_ordinal,
                "outcome": outcome,
                "reason": reason,
                "dispatched": dispatched,
                "receipt_operation_id": receipt_operation_id,
                "shadow_cost": shadow_cost,
                "salary_runway_days": salary_runway_days,
                "salary_runway_silver": salary_runway_silver,
                "started_at": started_at,
            },
        ),
    )[0]


def start_durable_cycle(
    profile: BotProfile,
    *,
    trigger: CycleTrigger | str,
    now: datetime | None = None,
) -> tuple[BotMaintenanceCycle, MaintenanceCycleState]:
    """Create or reopen the one durable cycle for a profile/ordinal."""

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("start_durable_cycle must run inside transaction.atomic()")
    current_time = now or timezone.now()
    normalized_trigger = CycleTrigger(trigger)
    last_ordinal = (
        BotMaintenanceCycle.objects.select_for_update()
        .filter(profile_id=profile.pk)
        .order_by("-cycle_ordinal")
        .values_list("cycle_ordinal", flat=True)
        .first()
    )
    ordinal = int(last_ordinal or 0) + 1
    cycle_id = f"vp-cycle-{int(profile.pk)}-{ordinal}-{uuid4().hex[:20]}"
    cycle = BotMaintenanceCycle.objects.create(
        cycle_id=cycle_id,
        interval_seed=cycle_id,
        profile=profile,
        cycle_ordinal=ordinal,
        trigger=normalized_trigger.value,
        max_actions=cycle_action_limit(normalized_trigger),
        started_at=current_time,
        current_action_state=BotMaintenanceCycle.ActionState.READY,
        next_slot_due_at=current_time,
        next_decision_at=current_time,
    )
    return cycle, new_cycle_state(cycle_id=cycle_id, cycle_ordinal=ordinal, trigger=normalized_trigger)


def close_durable_cycle_locked(
    cycle: BotMaintenanceCycle,
    *,
    reason: str = "",
    recovery_required: bool = False,
    completed_at: datetime | None = None,
    action_state: str | None = None,
    completion_source: str | None = None,
    extra_update_fields: Sequence[str] = (),
) -> BotMaintenanceCycle:
    """Close a cycle row already locked by the caller.

    ``extra_update_fields`` lets a caller fold pending action/payload changes
    into the close write, preserving one durable update for a terminal slot.
    """

    cycle.status = (
        BotMaintenanceCycle.Status.RECOVERY_REQUIRED if recovery_required else BotMaintenanceCycle.Status.COMPLETED
    )
    cycle.last_reason = str(reason or "")[:64]
    cycle.completed_at = completed_at or timezone.now()
    cycle.current_action_state = action_state or (
        BotMaintenanceCycle.ActionState.RECOVERY if recovery_required else BotMaintenanceCycle.ActionState.COMPLETED
    )
    if completion_source is not None:
        cycle.last_action_completion_source = str(completion_source)[:32]
    cycle.next_slot_due_at = None
    cycle.next_decision_at = None
    cycle.save(
        update_fields=[
            *dict.fromkeys(
                [
                    "status",
                    "last_reason",
                    "completed_at",
                    "current_action_state",
                    "last_action_completion_source",
                    "next_slot_due_at",
                    "next_decision_at",
                    "updated_at",
                    *extra_update_fields,
                ]
            )
        ]
    )
    return cycle


@transaction.atomic
def close_durable_cycle(cycle_id: str, *, reason: str = "", recovery_required: bool = False) -> BotMaintenanceCycle:
    cycle = BotMaintenanceCycle.objects.select_for_update().get(cycle_id=str(cycle_id))
    return close_durable_cycle_locked(
        cycle,
        reason=reason,
        recovery_required=recovery_required,
    )


__all__ = [
    "ARENA_CYCLE_ACTION_KINDS",
    "ARENA_CYCLE_ACTION_SLOTS",
    "ACTION_COMPLETION_SOURCE_CANDIDATE_EXHAUSTED",
    "ACTION_COMPLETION_SOURCE_MAINTENANCE_COMMIT",
    "NO_ACTION_CYCLE_KIND",
    "ORDINARY_ACTION_KINDS",
    "ORDINARY_CYCLE_ACTION_SLOTS",
    "ORDINARY_CYCLE_NEXT_START_MAX_DELAY",
    "ORDINARY_SLOT_INTERVAL_MINUTES_MAX",
    "ORDINARY_SLOT_INTERVAL_MINUTES_MIN",
    "CycleTrigger",
    "MaintenanceCycleError",
    "MaintenanceReasonCategory",
    "MaintenanceCycleState",
    "allocate_cycle_action",
    "append_durable_cycle_action",
    "append_durable_cycle_action_locked",
    "classify_maintenance_reason",
    "cycle_action_limit",
    "cycle_retry_due_at",
    "next_ordinary_slot_due_at",
    "ordinary_slot_interval_minutes",
    "finish_cycle",
    "close_durable_cycle_locked",
    "new_cycle_state",
    "record_durable_attempt",
    "record_durable_attempts",
    "record_durable_attempts_locked",
    "start_durable_cycle",
    "close_durable_cycle",
]
