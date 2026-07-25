"""
门客相关异常
"""

from __future__ import annotations

from typing import Any

from .base import GameError


class GuestError(GameError):
    """门客相关错误基类"""

    error_code = "GUEST_ERROR"


class GuestNotIdleError(GuestError):
    """门客不是空闲状态"""

    error_code = "GUEST_NOT_IDLE"

    def __init__(self, guest: Any, message: str | None = None):
        guest_name = getattr(guest, "display_name", str(guest))
        guest_status = getattr(guest, "status", "unknown")
        if message is None:
            status_labels = {
                "idle": "空闲",
                "working": "打工中",
                "deployed": "出征中",
                "arena": "竞技中",
                "injured": "重伤",
            }
            status_label = status_labels.get(guest_status, "未知状态")
            message = f"{guest_name} 当前状态为「{status_label}」，无法执行此操作"
        super().__init__(message, guest_name=guest_name, guest_status=guest_status)


class GuestInjuredError(GuestError):
    """门客重伤"""

    error_code = "GUEST_INJURED"

    def __init__(self, guest: Any, message: str | None = None):
        guest_name = getattr(guest, "display_name", str(guest))
        if message is None:
            message = f"{guest_name} 处于重伤状态，请先治疗"
        super().__init__(message, guest_name=guest_name)


class GuestMaxLevelError(GuestError):
    """门客已达等级上限"""

    error_code = "GUEST_MAX_LEVEL"

    def __init__(self, guest: Any, max_level: int = 100, message: str | None = None):
        guest_name = getattr(guest, "display_name", str(guest))
        if message is None:
            message = f"{guest_name} 已达等级上限 {max_level}"
        super().__init__(message, guest_name=guest_name, max_level=max_level)


class GuestTrainingInProgressError(GuestError):
    """门客正在训练中"""

    error_code = "GUEST_TRAINING_IN_PROGRESS"

    def __init__(self, guest: Any, message: str | None = None):
        guest_name = getattr(guest, "display_name", str(guest))
        if message is None:
            message = f"{guest_name} 已在等待升级完成"
        super().__init__(message, guest_name=guest_name)


class GuestTrainingUnavailableError(GuestError):
    """当前没有可进行训练时间缩减的门客"""

    error_code = "GUEST_TRAINING_UNAVAILABLE"
    default_message = "所有门客已达等级上限，无法使用该道具。"


class GuestAllocationResetError(GuestError):
    """门客当前没有可重置的已分配属性点"""

    error_code = "GUEST_ALLOCATION_RESET_ERROR"

    def __init__(self, guest: Any = None, message: str | None = None):
        guest_name = getattr(guest, "display_name", "该门客") if guest is not None else "该门客"
        if message is None:
            message = f"{guest_name} 没有分配过属性点，无需使用洗点卡"
        super().__init__(message, guest_name=guest_name)


class GuestNotRequirementError(GuestError):
    """门客属性不满足要求"""

    error_code = "GUEST_NOT_REQUIREMENT"

    def __init__(
        self,
        guest: Any,
        requirement_type: str,
        required: int,
        actual: int,
        message: str | None = None,
    ):
        guest_name = getattr(guest, "display_name", str(guest))
        if message is None:
            labels = {
                "level": "等级",
                "force": "武力",
                "intellect": "智力",
                "defense": "防御",
                "agility": "敏捷",
            }
            label = labels.get(requirement_type, "相关属性")
            message = f"{guest_name} {label}不足，需要 {required}，当前 {actual}"
        super().__init__(
            message,
            guest_name=guest_name,
            requirement_type=requirement_type,
            required=required,
            actual=actual,
        )


class GuestOwnershipError(GuestError):
    """门客不属于当前庄园"""

    error_code = "GUEST_OWNERSHIP_ERROR"

    def __init__(self, guest: Any = None, message: str | None = None):
        if message is None:
            message = "该门客不属于您的庄园"
        super().__init__(message)


class GuestAlreadyOwnedError(GuestError):
    """庄园已拥有该门客"""

    error_code = "GUEST_ALREADY_OWNED"

    def __init__(self, guest: Any = None, message: str | None = None):
        guest_name = getattr(guest, "display_name", getattr(guest, "name", "该门客")) if guest is not None else "该门客"
        if message is None:
            message = f"庄园已拥有门客「{guest_name}」，不可重复获得"
        super().__init__(message, guest_name=guest_name)


class GuestNotFoundError(GuestError):
    """门客不存在"""

    error_code = "GUEST_NOT_FOUND"
    default_message = "门客不存在"


class GuestItemOwnershipError(GuestError):
    """道具不存在或不属于当前庄园"""

    error_code = "GUEST_ITEM_OWNERSHIP_ERROR"
    default_message = "道具不存在或不属于您的庄园"


class GuestItemConfigurationError(GuestError):
    """门客培养道具配置无效"""

    error_code = "GUEST_ITEM_CONFIGURATION_ERROR"


class GuestSkillAlreadyLearnedError(GuestError):
    """门客已掌握该技能"""

    error_code = "GUEST_SKILL_ALREADY_LEARNED"

    def __init__(self, guest: Any = None, skill: Any = None, message: str | None = None):
        guest_name = getattr(guest, "display_name", "门客") if guest is not None else "门客"
        skill_name = getattr(skill, "name", "该技能") if skill is not None else "该技能"
        if message is None:
            message = f"{guest_name} 已掌握 {skill_name}"
        super().__init__(message, guest_name=guest_name, skill_name=skill_name)


class GuestSkillNotFoundError(GuestError):
    """门客技能不存在"""

    error_code = "GUEST_SKILL_NOT_FOUND"
    default_message = "未找到要操作的技能"


class GuestFullHpError(GuestError):
    """门客已满血"""

    error_code = "GUEST_FULL_HP"

    def __init__(self, guest: Any = None, message: str | None = None):
        guest_name = getattr(guest, "display_name", "门客") if guest else "门客"
        if message is None:
            message = f"{guest_name}已满血"
        super().__init__(message, guest_name=guest_name)


class InvalidHealAmountError(GuestError):
    """治疗量无效"""

    error_code = "INVALID_HEAL_AMOUNT"
    default_message = "药品未配置有效生命值"


class NoGuestsError(GuestError):
    """没有门客"""

    error_code = "NO_GUESTS"
    default_message = "您还没有门客"
