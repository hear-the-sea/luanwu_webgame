from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

from .maintenance_rules import ControlledActionDecision
from .projection import DevelopmentIntent, ProjectionRuleError, select_development_intent
from .random_context import RandomContext


class CandidateAssessmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    """Read-only eligibility result for one concrete maintenance intent."""

    intent: DevelopmentIntent
    resource_costs: tuple[tuple[str, int], ...] = ()
    resources_before_action: tuple[tuple[str, int], ...] = ()
    resources_after_action: tuple[tuple[str, int], ...] = ()
    controlled_decision: ControlledActionDecision | None = None
    projected_selected_power: int | None = None
    event_power_cap: int | None = None
    completion_seconds: int = 0
    queue_name: str = ""
    expected_strength_gain: int = 0
    selection_score: float | None = None
    rejection_reasons: tuple[str, ...] = ()
    next_affordable_at: datetime | None = None
    retryable: bool = False
    execution_metadata_key: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.intent, DevelopmentIntent):
            raise CandidateAssessmentError("intent must be a DevelopmentIntent")
        for field_name in (
            "resource_costs",
            "resources_before_action",
            "resources_after_action",
        ):
            rows = getattr(self, field_name)
            if tuple(sorted(rows)) != rows:
                raise CandidateAssessmentError(f"{field_name} must use canonical resource order")
            keys: set[str] = set()
            for resource, amount in rows:
                if not isinstance(resource, str) or not resource:
                    raise CandidateAssessmentError(f"{field_name} resource names must be non-empty strings")
                if resource in keys:
                    raise CandidateAssessmentError(f"{field_name} resource names must be unique")
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise CandidateAssessmentError(f"{field_name} amounts must be non-negative integers")
                keys.add(resource)
        if self.controlled_decision is not None and not isinstance(
            self.controlled_decision,
            ControlledActionDecision,
        ):
            raise CandidateAssessmentError("controlled_decision must be a ControlledActionDecision or None")
        if (self.projected_selected_power is None) != (self.event_power_cap is None):
            raise CandidateAssessmentError("projected selected power and event power cap must be provided together")
        for field_name in ("projected_selected_power", "event_power_cap"):
            value = getattr(self, field_name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise CandidateAssessmentError(f"{field_name} must be a non-negative integer or None")
        if (
            isinstance(self.completion_seconds, bool)
            or not isinstance(self.completion_seconds, int)
            or self.completion_seconds < 0
        ):
            raise CandidateAssessmentError("completion_seconds must be a non-negative integer")
        if not isinstance(self.queue_name, str) or (self.completion_seconds > 0 and not self.queue_name.strip()):
            raise CandidateAssessmentError("timed candidates must declare a queue_name")
        if (
            isinstance(self.expected_strength_gain, bool)
            or not isinstance(self.expected_strength_gain, int)
            or self.expected_strength_gain < 0
        ):
            raise CandidateAssessmentError("expected_strength_gain must be a non-negative integer")
        if self.selection_score is not None and not math.isfinite(float(self.selection_score)):
            raise CandidateAssessmentError("selection_score must be finite")
        if self.next_affordable_at is not None and (
            not isinstance(self.next_affordable_at, datetime)
            or self.next_affordable_at.tzinfo is None
            or self.next_affordable_at.utcoffset() is None
        ):
            raise CandidateAssessmentError("next_affordable_at must be a timezone-aware datetime or None")
        normalized_reasons = tuple(dict.fromkeys(str(reason).strip() for reason in self.rejection_reasons))
        if any(not reason for reason in normalized_reasons):
            raise CandidateAssessmentError("rejection reasons must be non-empty strings")
        if self.controlled_decision is not None:
            decision_reasons = tuple(reason.value for reason in self.controlled_decision.skipped_action_reasons)
            if any(reason not in normalized_reasons for reason in decision_reasons):
                raise CandidateAssessmentError("rejection reasons must include the controlled decision")
        if not isinstance(self.retryable, bool):
            raise CandidateAssessmentError("retryable must be a boolean")
        metadata_key = str(self.execution_metadata_key or self.intent.business_key).strip()
        if not metadata_key:
            raise CandidateAssessmentError("execution_metadata_key must not be empty")
        object.__setattr__(self, "rejection_reasons", normalized_reasons)
        object.__setattr__(self, "execution_metadata_key", metadata_key)

    @property
    def allowed(self) -> bool:
        return not self.rejection_reasons and (self.controlled_decision is None or self.controlled_decision.allowed)

    @property
    def primary_rejection_reason(self) -> str:
        return self.rejection_reasons[0] if self.rejection_reasons else ""

    def summary_payload(self) -> dict[str, object]:
        return {
            "action_kind": self.intent.action_kind,
            "allowed": self.allowed,
            "business_key": self.intent.business_key,
            "resource_costs": dict(self.resource_costs),
            "resources_before_action": dict(self.resources_before_action),
            "resources_after_action": dict(self.resources_after_action),
            "projected_selected_power": self.projected_selected_power,
            "event_power_cap": self.event_power_cap,
            "completion_seconds": self.completion_seconds,
            "queue_name": self.queue_name,
            "expected_strength_gain": self.expected_strength_gain,
            "selection_score": self.selection_score,
            "rejection_reasons": list(self.rejection_reasons),
            "next_affordable_at": (None if self.next_affordable_at is None else self.next_affordable_at.isoformat()),
            "retryable": self.retryable,
        }


def select_candidate_assessment(
    candidate_groups: tuple[tuple[DevelopmentIntent, ...], ...],
    *,
    assessments: tuple[CandidateAssessment, ...],
    context: RandomContext,
    optimization_bias: float,
    resource_barrier_group_indexes: frozenset[int] = frozenset(),
    resource_recovery_group_indexes: frozenset[int] = frozenset(),
) -> CandidateAssessment | None:
    """Select the first priority group containing an allowed candidate.

    If every candidate is rejected, return a deterministic primary rejection
    so the committed NO_ACTION result retains the attempted action and reason.
    """

    by_key = {assessment.intent.business_key: assessment for assessment in assessments}
    if len(by_key) != len(assessments):
        raise CandidateAssessmentError("candidate assessments must have unique business keys")

    def _selection_intent(assessment: CandidateAssessment) -> DevelopmentIntent:
        if assessment.selection_score is None:
            return assessment.intent
        return replace(assessment.intent, utility_score=assessment.selection_score)

    resource_rejection_reasons = frozenset({"insufficient_resource", "salary_runway_protected"})

    resource_barrier_assessment: CandidateAssessment | None = None
    resource_barrier_active = False
    for group_index, group in enumerate(candidate_groups):
        if (
            resource_barrier_active
            and group_index not in resource_barrier_group_indexes
            and group_index not in resource_recovery_group_indexes
        ):
            continue
        group_assessments = tuple(
            assessment for intent in group if (assessment := by_key.get(intent.business_key)) is not None
        )
        if group_index in resource_recovery_group_indexes:
            rejected = tuple(assessment for assessment in group_assessments if not assessment.allowed)
            if rejected and not any(assessment.allowed for assessment in group_assessments):
                # Economic recovery is an explicit priority group.  When its
                # production candidates are uniformly blocked, retain that
                # concrete recovery candidate as the no-action explanation;
                # a later minimum group must not hide the liquidity bottleneck.
                if all(
                    resource_rejection_reasons.intersection(assessment.rejection_reasons) for assessment in rejected
                ):
                    return min(
                        rejected,
                        key=lambda assessment: (
                            assessment.primary_rejection_reason,
                            assessment.intent.business_key,
                        ),
                    )
        if group_index in resource_barrier_group_indexes:
            rejected = tuple(assessment for assessment in group_assessments if not assessment.allowed)
            if (
                rejected
                and not any(assessment.allowed for assessment in group_assessments)
                and all(
                    resource_rejection_reasons.intersection(assessment.rejection_reasons) for assessment in rejected
                )
            ):
                # A mandatory priority group that is uniformly resource
                # blocked must remain visible as NO_ACTION.  Falling through
                # to a lower-priority action would hide the shortage and make
                # the next retry less likely to execute the intended work.
                if resource_barrier_assessment is None:
                    resource_barrier_assessment = min(
                        rejected,
                        key=lambda assessment: (
                            assessment.primary_rejection_reason,
                            assessment.intent.business_key,
                        ),
                    )
                resource_barrier_active = True
                continue
        allowed = tuple(
            _selection_intent(assessment)
            for intent in group
            if (assessment := by_key.get(intent.business_key)) is not None and assessment.allowed
        )
        selected = select_development_intent(
            allowed,
            context=context,
            optimization_bias=optimization_bias,
        )
        if selected is not None:
            return by_key[selected.business_key]

    if resource_barrier_assessment is not None:
        return resource_barrier_assessment

    for group in candidate_groups:
        rejected_assessments = tuple(
            assessment
            for intent in group
            for assessment in (by_key.get(intent.business_key),)
            if assessment is not None and not assessment.allowed
        )
        rejected_intents = tuple(_selection_intent(assessment) for assessment in rejected_assessments)
        try:
            selected = select_development_intent(
                rejected_intents,
                context=context,
                optimization_bias=optimization_bias,
            )
        except ProjectionRuleError as exc:
            raise CandidateAssessmentError("invalid rejected candidate group") from exc
        if selected is not None:
            return by_key[selected.business_key]

    return None


__all__ = [
    "CandidateAssessment",
    "CandidateAssessmentError",
    "select_candidate_assessment",
]
