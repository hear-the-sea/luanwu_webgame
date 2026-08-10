from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from .projection import DevelopmentIntent, StrengthSummary


class MaintenanceActionSpecError(ValueError):
    pass


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MaintenanceActionSpecError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MaintenanceActionSpecError(f"{field} must be a non-negative integer")
    return value


def _non_empty_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MaintenanceActionSpecError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_costs(
    values: tuple[tuple[str, int], ...],
) -> tuple[tuple[str, int], ...]:
    normalized: list[tuple[str, int]] = []
    seen: set[str] = set()
    for entry in values:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise MaintenanceActionSpecError("resource_costs must contain resource/amount pairs")
        resource, amount = entry
        resource = _non_empty_string(resource, field="resource_costs resource")
        amount = _positive_int(amount, field=f"resource_costs[{resource!r}]")
        if resource in seen:
            raise MaintenanceActionSpecError(f"duplicate resource cost: {resource}")
        seen.add(resource)
        normalized.append((resource, amount))
    canonical = tuple(sorted(normalized))
    if canonical != values:
        raise MaintenanceActionSpecError("resource_costs must use canonical resource order")
    return canonical


def _canonical_caps(
    values: tuple[tuple[str, int], ...],
) -> tuple[tuple[str, int], ...]:
    normalized: list[tuple[str, int]] = []
    seen: set[str] = set()
    for entry in values:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise MaintenanceActionSpecError("daily_caps must contain category/cap pairs")
        category, cap = entry
        category = _non_empty_string(category, field="daily_caps category")
        cap = _non_negative_int(cap, field=f"daily_caps[{category!r}]")
        if category in seen:
            raise MaintenanceActionSpecError(f"duplicate daily cap: {category}")
        seen.add(category)
        normalized.append((category, cap))
    canonical = tuple(sorted(normalized))
    if canonical != values:
        raise MaintenanceActionSpecError("daily_caps must use canonical category order")
    return canonical


@dataclass(frozen=True, slots=True)
class SkillLearningActionSpec:
    action_kind: ClassVar[str] = "skill_learning"

    guest_id: int
    inventory_item_id: int
    item_template_id: int
    item_key: str
    item_quantity_before: int
    skill_id: int
    skill_key: str
    source: str = "book"

    def __post_init__(self) -> None:
        for field in ("guest_id", "skill_id"):
            object.__setattr__(
                self,
                field,
                _positive_int(getattr(self, field), field=field),
            )
        source = _non_empty_string(self.source, field="source")
        if source not in {"book", "virtual"}:
            raise MaintenanceActionSpecError("skill learning source must be book or virtual")
        object.__setattr__(self, "source", source)
        if source == "book":
            object.__setattr__(
                self,
                "item_template_id",
                _positive_int(self.item_template_id, field="item_template_id"),
            )
            for field in ("inventory_item_id", "item_quantity_before"):
                object.__setattr__(
                    self,
                    field,
                    _positive_int(getattr(self, field), field=field),
                )
        else:
            object.__setattr__(
                self,
                "item_template_id",
                _positive_int(self.item_template_id, field="item_template_id"),
            )
            for field in ("inventory_item_id", "item_quantity_before"):
                object.__setattr__(
                    self,
                    field,
                    _non_negative_int(getattr(self, field), field=field),
                )
        object.__setattr__(
            self,
            "item_key",
            _non_empty_string(self.item_key, field="item_key"),
        )
        object.__setattr__(
            self,
            "skill_key",
            _non_empty_string(self.skill_key, field="skill_key"),
        )

    @property
    def business_key(self) -> str:
        suffix = "" if self.source == "book" else ":source:virtual"
        return f"skill_learning:guest:{self.guest_id}:skill:{self.skill_key}{suffix}"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "action_kind": self.action_kind,
            "business_key": self.business_key,
            "guest_id": self.guest_id,
            "inventory_item_id": self.inventory_item_id,
            "item_key": self.item_key,
            "item_quantity_before": self.item_quantity_before,
            "item_template_id": self.item_template_id,
            "skill_id": self.skill_id,
            "skill_key": self.skill_key,
        }
        if self.source != "book":
            payload["source"] = self.source
        return payload


@dataclass(frozen=True, slots=True)
class EquipmentEquipActionSpec:
    action_kind: ClassVar[str] = "equipment_equip"

    guest_id: int
    inventory_item_id: int
    item_template_id: int
    item_key: str
    item_quantity_before: int
    slot: str
    source: str = "inventory"

    def __post_init__(self) -> None:
        for field in ("guest_id", "item_template_id"):
            object.__setattr__(
                self,
                field,
                _positive_int(getattr(self, field), field=field),
            )
        source = _non_empty_string(self.source, field="source")
        if source not in {"inventory", "virtual"}:
            raise MaintenanceActionSpecError("equipment source must be inventory or virtual")
        object.__setattr__(self, "source", source)
        for field in ("inventory_item_id", "item_quantity_before"):
            object.__setattr__(
                self,
                field,
                (
                    _positive_int(getattr(self, field), field=field)
                    if source == "inventory"
                    else _non_negative_int(getattr(self, field), field=field)
                ),
            )
        object.__setattr__(
            self,
            "item_key",
            _non_empty_string(self.item_key, field="item_key"),
        )
        object.__setattr__(
            self,
            "slot",
            _non_empty_string(self.slot, field="slot"),
        )

    @property
    def business_key(self) -> str:
        suffix = "" if self.source == "inventory" else ":source:virtual"
        return f"equipment_equip:guest:{self.guest_id}:item:{self.item_key}{suffix}"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "action_kind": self.action_kind,
            "business_key": self.business_key,
            "guest_id": self.guest_id,
            "inventory_item_id": self.inventory_item_id,
            "item_key": self.item_key,
            "item_quantity_before": self.item_quantity_before,
            "item_template_id": self.item_template_id,
            "slot": self.slot,
        }
        if self.source != "inventory":
            payload["source"] = self.source
        return payload


@dataclass(frozen=True, slots=True)
class InventoryAcquisitionActionSpec:
    action_kind: ClassVar[str] = "inventory_acquisition"

    item_template_id: int
    item_key: str
    daily_caps: tuple[tuple[str, int], ...]
    quantity: int = 1
    batch_id: str = ""
    batch_items: tuple[tuple[int, str, tuple[tuple[str, int], ...], int], ...] = ()
    batch_draws: tuple[tuple[int, str, str, float], ...] = ()
    source: str = "inventory"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "item_template_id",
            _positive_int(self.item_template_id, field="item_template_id"),
        )
        object.__setattr__(
            self,
            "item_key",
            _non_empty_string(self.item_key, field="item_key"),
        )
        object.__setattr__(self, "daily_caps", _canonical_caps(self.daily_caps))
        object.__setattr__(self, "quantity", _positive_int(self.quantity, field="quantity"))
        object.__setattr__(self, "batch_id", str(self.batch_id).strip())
        source = _non_empty_string(self.source, field="source")
        if source not in {"inventory", "virtual"}:
            raise MaintenanceActionSpecError("inventory acquisition source must be inventory or virtual")
        object.__setattr__(self, "source", source)
        normalized_batch_items: list[tuple[int, str, tuple[tuple[str, int], ...], int]] = []
        for index, batch_entry in enumerate(self.batch_items):
            if not isinstance(batch_entry, tuple) or len(batch_entry) != 4:
                raise MaintenanceActionSpecError(f"batch_items[{index}] must contain four values")
            template_id, batch_item_key, daily_caps, quantity = batch_entry
            normalized_batch_items.append(
                (
                    _positive_int(template_id, field=f"batch_items[{index}].template_id"),
                    _non_empty_string(batch_item_key, field=f"batch_items[{index}].item_key"),
                    _canonical_caps(daily_caps),
                    _positive_int(quantity, field=f"batch_items[{index}].quantity"),
                )
            )
        if normalized_batch_items and self.quantity != 1:
            raise MaintenanceActionSpecError("batch_items cannot be combined with quantity")
        object.__setattr__(self, "batch_items", tuple(normalized_batch_items))
        if len(self.batch_draws) > 5:
            raise MaintenanceActionSpecError("batch_draws must contain at most five draws")
        normalized_draws: list[tuple[int, str, str, float]] = []
        seen_ordinals: set[int] = set()
        for index, draw_entry in enumerate(self.batch_draws):
            if not isinstance(draw_entry, tuple) or len(draw_entry) != 4:
                raise MaintenanceActionSpecError(f"batch_draws[{index}] must contain four values")
            draw_ordinal, draw_color, draw_item_key, weight = draw_entry
            ordinal = _positive_int(draw_ordinal, field=f"batch_draws[{index}].draw_ordinal")
            color = _non_empty_string(draw_color, field=f"batch_draws[{index}].color")
            item_key = _non_empty_string(draw_item_key, field=f"batch_draws[{index}].item_key")
            try:
                normalized_weight = float(weight)
            except (TypeError, ValueError) as exc:
                raise MaintenanceActionSpecError(f"batch_draws[{index}].weight must be finite and positive") from exc
            if (
                normalized_weight <= 0
                or normalized_weight != normalized_weight
                or normalized_weight
                in {
                    float("inf"),
                    float("-inf"),
                }
            ):
                raise MaintenanceActionSpecError(f"batch_draws[{index}].weight must be finite and positive")
            if ordinal in seen_ordinals:
                raise MaintenanceActionSpecError("batch_draws draw ordinals must be unique")
            if ordinal != index + 1:
                raise MaintenanceActionSpecError("batch_draws draw ordinals must start at one and be contiguous")
            seen_ordinals.add(ordinal)
            normalized_draws.append((ordinal, color, item_key, normalized_weight))
        object.__setattr__(self, "batch_draws", tuple(normalized_draws))

    @property
    def business_key(self) -> str:
        if self.quantity == 1 and not self.batch_id and not self.batch_items:
            suffix = ":source:virtual" if self.source == "virtual" else ""
            return f"inventory_acquisition:item:{self.item_key}{suffix}"
        batch_suffix = f":batch:{self.batch_id}" if self.batch_id else ""
        item_suffix = f":items:{len(self.batch_items)}" if self.batch_items else f":quantity:{self.quantity}"
        source_suffix = ":source:virtual" if self.source == "virtual" else ""
        return f"inventory_acquisition:item:{self.item_key}{item_suffix}{batch_suffix}{source_suffix}"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "action_kind": self.action_kind,
            "business_key": self.business_key,
            "daily_caps": dict(self.daily_caps),
            "item_key": self.item_key,
            "item_template_id": self.item_template_id,
        }
        if self.quantity != 1:
            payload["quantity"] = self.quantity
        if self.batch_id:
            payload["batch_id"] = self.batch_id
        if self.batch_items:
            payload["batch_items"] = [
                {
                    "daily_caps": dict(daily_caps),
                    "item_key": item_key,
                    "quantity": quantity,
                    "item_template_id": template_id,
                }
                for template_id, item_key, daily_caps, quantity in self.batch_items
            ]
        if self.batch_draws:
            payload["batch_draws"] = [
                {
                    "color": color,
                    "draw_ordinal": ordinal,
                    "item_key": item_key,
                    "weight": weight,
                }
                for ordinal, color, item_key, weight in self.batch_draws
            ]
        if self.source == "virtual":
            payload["source"] = self.source
        return payload


@dataclass(frozen=True, slots=True)
class GuestRecruitmentActionSpec:
    """Deterministic, low-quality roster expansion for a V2 quantity phase."""

    action_kind: ClassVar[str] = "guest_recruitment"

    template_id: int
    template_key: str
    rarity: str
    archetype: str
    quantity: int
    rng_seed: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_id", _positive_int(self.template_id, field="template_id"))
        object.__setattr__(self, "template_key", _non_empty_string(self.template_key, field="template_key"))
        object.__setattr__(self, "rarity", _non_empty_string(self.rarity, field="rarity"))
        object.__setattr__(self, "archetype", _non_empty_string(self.archetype, field="archetype"))
        quantity = _positive_int(self.quantity, field="quantity")
        if quantity > 2:
            raise MaintenanceActionSpecError("guest recruitment quantity must not exceed two guests per cycle")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "rng_seed", _non_negative_int(self.rng_seed, field="rng_seed"))

    @property
    def business_key(self) -> str:
        return f"guest_recruitment:{self.template_key}:quantity:{self.quantity}:seed:{self.rng_seed}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "archetype": self.archetype,
            "business_key": self.business_key,
            "quantity": self.quantity,
            "rarity": self.rarity,
            "rng_seed": self.rng_seed,
            "template_id": self.template_id,
            "template_key": self.template_key,
        }


@dataclass(frozen=True, slots=True)
class BuildingUpgradeActionSpec:
    action_kind: ClassVar[str] = "building_upgrade"

    building_id: int
    building_key: str
    level_before: int
    level_after: int
    resource_costs: tuple[tuple[str, int], ...]
    prestige_after: int
    core_building_level_after: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "building_id",
            _positive_int(self.building_id, field="building_id"),
        )
        object.__setattr__(
            self,
            "building_key",
            _non_empty_string(self.building_key, field="building_key"),
        )
        object.__setattr__(
            self,
            "level_before",
            _positive_int(self.level_before, field="level_before"),
        )
        object.__setattr__(
            self,
            "level_after",
            _positive_int(self.level_after, field="level_after"),
        )
        if self.level_after != self.level_before + 1:
            raise MaintenanceActionSpecError("building upgrade must advance exactly one level")
        object.__setattr__(
            self,
            "resource_costs",
            _canonical_costs(self.resource_costs),
        )
        object.__setattr__(
            self,
            "prestige_after",
            _non_negative_int(self.prestige_after, field="prestige_after"),
        )
        object.__setattr__(
            self,
            "core_building_level_after",
            _positive_int(
                self.core_building_level_after,
                field="core_building_level_after",
            ),
        )

    @property
    def business_key(self) -> str:
        return f"building_upgrade:{self.building_key}:" f"{self.level_before}->{self.level_after}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "building_id": self.building_id,
            "building_key": self.building_key,
            "business_key": self.business_key,
            "core_building_level_after": self.core_building_level_after,
            "level_after": self.level_after,
            "level_before": self.level_before,
            "prestige_after": self.prestige_after,
            "resource_costs": dict(self.resource_costs),
        }


@dataclass(frozen=True, slots=True)
class TechnologyUpgradeActionSpec:
    action_kind: ClassVar[str] = "technology_upgrade"

    technology_key: str
    level_before: int
    level_after: int
    resource_costs: tuple[tuple[str, int], ...]
    prestige_after: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "technology_key",
            _non_empty_string(self.technology_key, field="technology_key"),
        )
        object.__setattr__(
            self,
            "level_before",
            _non_negative_int(self.level_before, field="level_before"),
        )
        object.__setattr__(
            self,
            "level_after",
            _positive_int(self.level_after, field="level_after"),
        )
        if self.level_after != self.level_before + 1:
            raise MaintenanceActionSpecError("technology upgrade must advance exactly one level")
        object.__setattr__(
            self,
            "resource_costs",
            _canonical_costs(self.resource_costs),
        )
        object.__setattr__(
            self,
            "prestige_after",
            _non_negative_int(self.prestige_after, field="prestige_after"),
        )

    @property
    def business_key(self) -> str:
        return f"technology_upgrade:{self.technology_key}:" f"{self.level_before}->{self.level_after}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "action_kind": self.action_kind,
            "business_key": self.business_key,
            "level_after": self.level_after,
            "level_before": self.level_before,
            "prestige_after": self.prestige_after,
            "resource_costs": dict(self.resource_costs),
            "technology_key": self.technology_key,
        }


type MaintenanceActionSpec = (
    SkillLearningActionSpec
    | EquipmentEquipActionSpec
    | InventoryAcquisitionActionSpec
    | GuestRecruitmentActionSpec
    | BuildingUpgradeActionSpec
    | TechnologyUpgradeActionSpec
)


def maintenance_action_spec_payload(
    spec: MaintenanceActionSpec | None,
) -> dict[str, Any] | None:
    return None if spec is None else spec.to_payload()


def _validate_component_changes(
    before: StrengthSummary,
    after: StrengthSummary,
    *,
    allowed: frozenset[str],
) -> None:
    if before.components.keys() != after.components.keys():
        raise MaintenanceActionSpecError("maintenance action strength component keys must not change")
    changed = {key for key in before.components if before.components[key] != after.components[key]}
    unexpected = sorted(changed - allowed)
    if unexpected:
        raise MaintenanceActionSpecError(
            "maintenance action changed unsupported strength components: " + ", ".join(unexpected)
        )


def project_maintenance_action_intent(
    *,
    spec: MaintenanceActionSpec,
    source_prestige_band: str,
    target_prestige_band: str,
    strength_before: StrengthSummary,
    strength_after: StrengthSummary,
    utility_score: float,
    defer_completion: bool = False,
) -> DevelopmentIntent:
    if not isinstance(strength_before, StrengthSummary) or not isinstance(strength_after, StrengthSummary):
        raise MaintenanceActionSpecError("strength_before and strength_after must be StrengthSummary values")

    if isinstance(
        spec,
        (SkillLearningActionSpec, InventoryAcquisitionActionSpec),
    ):
        if strength_after != strength_before:
            raise MaintenanceActionSpecError(f"{spec.action_kind} must not change frozen strength")
    elif isinstance(spec, EquipmentEquipActionSpec):
        _validate_component_changes(
            strength_before,
            strength_after,
            allowed=frozenset({"arena_lineup_power"}),
        )
        if strength_after.components["arena_lineup_power"] < strength_before.components["arena_lineup_power"]:
            raise MaintenanceActionSpecError("equipment maintenance must not reduce arena lineup power")
    elif isinstance(spec, BuildingUpgradeActionSpec):
        _validate_component_changes(
            strength_before,
            strength_after,
            allowed=frozenset({"core_building_level", "prestige"}),
        )
        expected_core_level = (
            strength_before.components["core_building_level"] if defer_completion else spec.core_building_level_after
        )
        if (
            strength_after.components["core_building_level"] != expected_core_level
            or strength_after.components["prestige"] != spec.prestige_after
        ):
            raise MaintenanceActionSpecError("building strength projection does not match its action spec")
    elif isinstance(spec, TechnologyUpgradeActionSpec):
        _validate_component_changes(
            strength_before,
            strength_after,
            allowed=frozenset({"prestige"}),
        )
        if strength_after.components["prestige"] != spec.prestige_after:
            raise MaintenanceActionSpecError("technology strength projection does not match its action spec")
    elif isinstance(spec, GuestRecruitmentActionSpec):
        _validate_component_changes(
            strength_before,
            strength_after,
            allowed=frozenset({"arena_lineup_power", "guest_count", "max_guest_level"}),
        )
        if strength_after.components["guest_count"] != strength_before.components["guest_count"] + spec.quantity:
            raise MaintenanceActionSpecError("guest recruitment strength projection has an invalid guest count")
        if strength_after.components["max_guest_level"] < strength_before.components["max_guest_level"]:
            raise MaintenanceActionSpecError("guest recruitment must not reduce the maximum guest level")
        if strength_after.components["arena_lineup_power"] < strength_before.components["arena_lineup_power"]:
            raise MaintenanceActionSpecError("guest recruitment must not reduce arena lineup power")
    else:
        raise MaintenanceActionSpecError(f"unsupported maintenance action spec: {type(spec).__name__}")

    return DevelopmentIntent(
        business_key=spec.business_key,
        action_kind=spec.action_kind,
        source_prestige_band=source_prestige_band,
        target_prestige_band=target_prestige_band,
        strength_before=strength_before,
        strength_after=strength_after,
        utility_score=utility_score,
    )


__all__ = [
    "BuildingUpgradeActionSpec",
    "EquipmentEquipActionSpec",
    "GuestRecruitmentActionSpec",
    "InventoryAcquisitionActionSpec",
    "MaintenanceActionSpec",
    "MaintenanceActionSpecError",
    "SkillLearningActionSpec",
    "TechnologyUpgradeActionSpec",
    "maintenance_action_spec_payload",
    "project_maintenance_action_intent",
]
