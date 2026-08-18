"""
资源和容量相关异常
"""

from __future__ import annotations

from common.constants.resources import ResourceType

from .base import GameError

# ============ 资源相关异常 ============


class ResourceError(GameError):
    """资源相关错误基类"""

    error_code = "RESOURCE_ERROR"


class InsufficientResourceError(ResourceError):
    """资源不足"""

    error_code = "INSUFFICIENT_RESOURCE"

    def __init__(
        self,
        resource_type: str,
        required: int,
        available: int,
        message: str | None = None,
    ):
        self.resource_type = resource_type
        self.required = required
        self.available = available
        if message is None:
            labels = dict(ResourceType.choices)
            label = labels.get(resource_type, "资源")
            message = f"{label}不足，需要 {required}，当前 {available}"
        super().__init__(
            message,
            resource_type=resource_type,
            required=required,
            available=available,
        )


class InsufficientSilverError(InsufficientResourceError):
    """银两不足（常用快捷类）"""

    error_code = "INSUFFICIENT_SILVER"

    def __init__(self, required: int, available: int, message: str | None = None):
        super().__init__("silver", required, available, message)


# ============ 容量相关异常 ============


class CapacityError(GameError):
    """容量相关错误基类"""

    error_code = "CAPACITY_ERROR"


class GuestCapacityFullError(CapacityError):
    """门客容量已满"""

    error_code = "GUEST_CAPACITY_FULL"
    default_message = "聚贤庄容量已满，升级后再招募新的门客"


class RetainerCapacityFullError(CapacityError):
    """家丁容量已满"""

    error_code = "RETAINER_CAPACITY_FULL"
    default_message = "家丁房容量已满，无法继续安置家丁"


class TroopCapacityFullError(CapacityError):
    """庄园内护院容量已满"""

    error_code = "TROOP_CAPACITY_FULL"

    def __init__(
        self,
        required: int,
        available: int,
        capacity: int,
        message: str | None = None,
    ):
        if message is None:
            message = f"庄园内护院容量不足，需要增加{required}名，当前仅剩{available}名（上限{capacity}名）"
        super().__init__(
            message,
            required=required,
            available=available,
            capacity=capacity,
        )


class SkillSlotFullError(CapacityError):
    """技能位已满"""

    error_code = "SKILL_SLOT_FULL"
    default_message = "技能位已满"


class EquipmentSlotFullError(CapacityError):
    """装备槽位已满"""

    error_code = "EQUIPMENT_SLOT_FULL"

    def __init__(self, slot: str, message: str | None = None):
        if message is None:
            message = "该槽位已满"
        super().__init__(message, slot=slot)


class TreasuryCapacityFullError(CapacityError):
    """藏宝阁空间不足"""

    error_code = "TREASURY_CAPACITY_FULL"

    def __init__(
        self,
        required_space: int,
        remaining_space: int,
        message: str | None = None,
    ):
        if message is None:
            message = f"藏宝阁空间不足，剩余空间：{remaining_space}，需要空间：{required_space}"
        super().__init__(
            message,
            required_space=required_space,
            remaining_space=remaining_space,
        )


class InsufficientSpaceError(CapacityError):
    """空间不足"""

    error_code = "INSUFFICIENT_SPACE"

    def __init__(
        self,
        location: str,
        available: int,
        required: int,
        message: str | None = None,
    ):
        location_names = {
            "treasury": "藏宝阁",
            "warehouse": "仓库",
        }
        location_name = location_names.get(location, "对应位置")
        if message is None:
            message = f"{location_name}空间不足，剩余空间：{available}，需要空间：{required}"
        super().__init__(
            message,
            location=location,
            available=available,
            required=required,
        )
