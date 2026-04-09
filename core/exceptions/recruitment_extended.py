"""
募兵扩展异常类

为募兵系统提供更详细的错误类型，支持从 bool 返回值迁移到异常处理。
"""

from __future__ import annotations

from .gameplay import TroopRecruitmentError


class TroopRecruitmentNotFoundError(TroopRecruitmentError):
    """募兵记录不存在"""

    code = "troop_recruitment_not_found"

    def __init__(self, recruitment_id: int | None = None, message: str | None = None):
        if message is None:
            message = f"募兵记录不存在: recruitment_id={recruitment_id}"
        super().__init__(message, recruitment_id=recruitment_id)


class TroopRecruitmentNotReadyError(TroopRecruitmentError):
    """募兵尚未完成"""

    code = "troop_recruitment_not_ready"

    def __init__(self, complete_at: str | None = None, message: str | None = None):
        if message is None:
            message = f"募兵尚未完成，预计完成时间: {complete_at}"
        super().__init__(message, complete_at=complete_at)


class TroopTemplateNotFoundError(TroopRecruitmentError):
    """战斗兵种模板不存在"""

    code = "troop_template_not_found"

    def __init__(self, troop_key: str, message: str | None = None):
        if message is None:
            message = f"战斗兵种模板不存在: troop_key={troop_key}"
        super().__init__(message, troop_key=troop_key)
