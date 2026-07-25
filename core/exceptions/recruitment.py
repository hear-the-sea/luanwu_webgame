"""
招募和属性点相关异常
"""

from __future__ import annotations

from .base import GameError

# ============ 招募相关异常 ============


class RecruitmentError(GameError):
    """招募相关错误基类"""

    error_code = "RECRUITMENT_ERROR"


class PoolNotConfiguredError(RecruitmentError):
    """卡池未配置"""

    error_code = "POOL_NOT_CONFIGURED"
    default_message = "卡池尚未配置门客"


class NoTemplateAvailableError(RecruitmentError):
    """没有可用模板"""

    error_code = "NO_TEMPLATE_AVAILABLE"
    default_message = "缺少可用的门客模板，请联系管理员"


class RecruitmentAlreadyInProgressError(RecruitmentError):
    """已有招募进行中"""

    error_code = "RECRUITMENT_ALREADY_IN_PROGRESS"
    default_message = "已有招募正在进行中，请等待当前招募完成。"


class RecruitmentDailyLimitExceededError(RecruitmentError):
    """卡池当日招募次数达到上限"""

    error_code = "RECRUITMENT_DAILY_LIMIT_EXCEEDED"

    def __init__(self, pool_name: str, daily_limit: int, message: str | None = None):
        resolved_message = message or f"{pool_name}今日招募次数已达上限（{daily_limit}次）"
        super().__init__(resolved_message, pool_name=pool_name, daily_limit=int(daily_limit))


class RecruitmentItemOwnershipError(RecruitmentError):
    """招募相关道具不存在或不属于当前庄园"""

    error_code = "RECRUITMENT_ITEM_OWNERSHIP_ERROR"
    default_message = "道具不存在或不属于您的庄园"


class RecruitmentCandidateStateError(RecruitmentError):
    """候选门客状态无效"""

    error_code = "RECRUITMENT_CANDIDATE_STATE_ERROR"
    default_message = "候选门客不存在或已处理"


# ============ 属性点相关异常 ============


class AttributePointError(GameError):
    """属性点相关错误基类"""

    error_code = "ATTRIBUTE_POINT_ERROR"


class InvalidAllocationError(AttributePointError):
    """无效的加点请求"""

    error_code = "INVALID_ALLOCATION"

    def __init__(self, reason: str, message: str | None = None):
        if message is None:
            if reason == "zero_points":
                message = "加点数量必须大于 0"
            elif reason == "insufficient":
                message = "属性点不足"
            elif reason == "unknown_attribute":
                message = "未知的加点属性"
            elif reason == "attribute_overflow":
                message = "属性值已达上限，无法继续加点"
            else:
                message = "无效的加点请求"
        super().__init__(message, reason=reason)
