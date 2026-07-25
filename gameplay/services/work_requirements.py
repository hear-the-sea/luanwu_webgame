from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WORK_REQUIREMENT_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("level", "等级", "required_level", "level"),
    ("force", "武力", "required_force", "force"),
    ("intellect", "智力", "required_intellect", "intellect"),
    ("defense", "防御", "required_defense", "defense_stat"),
    ("agility", "敏捷", "required_agility", "agility"),
)


@dataclass(frozen=True, slots=True)
class WorkRequirement:
    key: str
    label: str
    required: int
    guest_field: str


@dataclass(frozen=True, slots=True)
class WorkRequirementResult:
    key: str
    label: str
    required: int
    actual: int

    @property
    def missing(self) -> int:
        return max(0, self.required - self.actual)

    @property
    def surplus(self) -> int:
        return max(0, self.actual - self.required)

    @property
    def met(self) -> bool:
        return self.missing == 0


@dataclass(frozen=True, slots=True)
class WorkEligibility:
    requirements: tuple[WorkRequirementResult, ...]

    @property
    def missing_requirements(self) -> tuple[WorkRequirementResult, ...]:
        return tuple(requirement for requirement in self.requirements if not requirement.met)

    @property
    def requirements_met(self) -> bool:
        return not self.missing_requirements

    @property
    def level_missing(self) -> int:
        return next((requirement.missing for requirement in self.requirements if requirement.key == "level"), 0)

    @property
    def attribute_missing(self) -> int:
        return sum(requirement.missing for requirement in self.requirements if requirement.key != "level")

    @property
    def attribute_surplus(self) -> int:
        return sum(requirement.surplus for requirement in self.requirements if requirement.key != "level")


def get_enabled_work_requirements(work_template: Any) -> tuple[WorkRequirement, ...]:
    requirements: list[WorkRequirement] = []
    for key, label, work_field, guest_field in WORK_REQUIREMENT_FIELDS:
        required = int(getattr(work_template, work_field, 0) or 0)
        if key != "level" and required == 0:
            continue
        requirements.append(
            WorkRequirement(
                key=key,
                label=label,
                required=required,
                guest_field=guest_field,
            )
        )
    return tuple(requirements)


def evaluate_work_requirements(guest: Any, work_template: Any) -> WorkEligibility:
    results = tuple(
        WorkRequirementResult(
            key=requirement.key,
            label=requirement.label,
            required=requirement.required,
            actual=int(getattr(guest, requirement.guest_field, 0) or 0),
        )
        for requirement in get_enabled_work_requirements(work_template)
    )
    return WorkEligibility(requirements=results)
