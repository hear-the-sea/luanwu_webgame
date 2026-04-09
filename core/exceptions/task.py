"""
任务调度相关异常
"""

from __future__ import annotations

from .base import GameError

# ============ 任务调度相关异常 ============


class TaskDispatchError(GameError):
    """任务调度失败基类"""

    error_code = "task_dispatch_failed"

    def __init__(self, reason: str = "未知原因", message: str | None = None):
        self.reason = reason
        if message is None:
            message = f"任务调度失败：{reason}"
        super().__init__(message, reason=reason)


class TaskRescheduleError(TaskDispatchError):
    """任务需要重新调度"""

    error_code = "task_reschedule_required"

    def __init__(self, reason: str = "调度服务暂时不可用", message: str | None = None):
        self.reason = reason
        if message is None:
            message = f"任务需要重新调度：{reason}"
        super().__init__(reason, message)
