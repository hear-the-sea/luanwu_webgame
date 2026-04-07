from __future__ import annotations

import logging
from collections.abc import Callable

from django.db import transaction

from core.utils.infrastructure import InfrastructureExceptions, combine_infrastructure_exceptions
from core.utils.task_monitoring import increment_degraded_counter

DEFAULT_SIDE_EFFECT_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions()


def schedule_best_effort_after_commit(
    callback: Callable[[], None],
    *,
    logger: logging.Logger,
    log_message: str,
    expected_exceptions: InfrastructureExceptions = DEFAULT_SIDE_EFFECT_EXCEPTIONS,
    degraded_component: str | None = None,
) -> None:
    def _run() -> None:
        try:
            callback()
        except expected_exceptions:
            logger.warning(log_message, exc_info=True)
            if degraded_component:
                increment_degraded_counter(degraded_component)

    transaction.on_commit(_run)


__all__ = [
    "DEFAULT_SIDE_EFFECT_EXCEPTIONS",
    "schedule_best_effort_after_commit",
]
