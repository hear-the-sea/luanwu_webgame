from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Optional

from celery.exceptions import TimeoutError as CeleryTimeoutError
from django.core.cache import cache
from kombu.exceptions import OperationalError

from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

CELERY_DISPATCH_INFRA_EXCEPTIONS = (
    OperationalError,
    ConnectionError,
    OSError,
    TimeoutError,
    CeleryTimeoutError,
)


def _rollback_dedup_gate(dedup_key: str, *, logger: Optional[logging.Logger]) -> None:
    try:
        cache.delete(dedup_key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        if logger:
            logger.debug("celery dispatch dedup rollback failed: %s", dedup_key, exc_info=True)


def safe_apply_async(
    task: Any,
    *,
    args: Optional[Iterable[Any]] = None,
    kwargs: Optional[Mapping[str, Any]] = None,
    countdown: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
    log_message: str = "celery task dispatch failed",
    log_extra: Optional[Mapping[str, Any]] = None,
    raise_on_failure: bool = False,
) -> bool:
    """
    Best-effort Celery dispatch wrapper.

    Many callsites treat task dispatch as an optimization (fallback scanners/refresh loops exist),
    so we standardize the behavior: swallow dispatch exceptions but log them consistently.

    Semantics:
    - Returns True when the task is successfully enqueued into the broker.
    - Returns False when dispatch fails with an infrastructure exception covered by
      ``CELERY_DISPATCH_INFRA_EXCEPTIONS``.
      In this case ``celery_dispatch_failed`` degraded counter is incremented via
      ``core.utils.task_monitoring.increment_degraded_counter``.
    - Non-infrastructure dispatch errors continue to raise.
    - Does NOT raise on infrastructure failure by default (``raise_on_failure=False``).

    Caller responsibility:
    - Check the return value and decide whether synchronous fallback is needed.
    - For "best-effort" callsites (notifications, cache refreshes): silently skip on False.
    - For "must-execute" callsites (state machine transitions, resource mutations): provide
      a synchronous fallback path when False is returned.
    - Use ``log_extra`` to attach business context fields to the single dispatch failure log.

    When ``degraded=True`` appears in log records or the degraded counter increments,
    it indicates an infrastructure issue with the broker (Redis unavailable, network partition,
    etc.) rather than a bug in the task itself.
    """
    try:
        task.apply_async(args=list(args or []), kwargs=dict(kwargs or {}), countdown=countdown)
        return True
    except CELERY_DISPATCH_INFRA_EXCEPTIONS:
        if logger:
            extra = dict(log_extra or {})
            extra.update({"degraded": True, "component": "celery_dispatch"})
            logger.warning(log_message, exc_info=True, extra=extra)
        from core.utils.task_monitoring import increment_degraded_counter

        increment_degraded_counter("celery_dispatch_failed")
        if raise_on_failure:
            raise
        return False


def safe_apply_async_with_dedup(
    task: Any,
    *,
    dedup_key: str,
    dedup_timeout: int,
    args: Optional[Iterable[Any]] = None,
    kwargs: Optional[Mapping[str, Any]] = None,
    countdown: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
    log_message: str = "celery task dispatch failed",
    raise_on_failure: bool = False,
) -> bool:
    """
    Best-effort Celery dispatch with cache-based dedup gate.

    Returns True when dispatch succeeded, or when another worker/request already dispatched
    the same dedup key in the dedup window. Returns False only when dispatch fails.

    Semantics (extends ``safe_apply_async``):
    - Dedup gate: ``cache.add(dedup_key, ...)`` is used as an atomic set-if-not-exists gate.
      If the key already exists (another dispatch won the race), returns True immediately
      without re-dispatching (idempotent skip).
    - On dispatch failure after acquiring the dedup gate: the gate key is automatically
      deleted (rolled back) so that subsequent retries within the dedup window are not
      incorrectly suppressed.
    - If the cache backend itself is unavailable when checking the dedup gate, a warning is
      logged and dispatch proceeds without dedup protection (fail-open for availability).

    Caller responsibility:
    - Same as ``safe_apply_async``: check the return value for "must-execute" scenarios.
    - Choose ``dedup_timeout`` to match the expected task execution window; a timeout that
      is too short allows duplicate dispatches, too long suppresses legitimate retries.

    When ``degraded=True`` appears in log records it indicates either the broker or the
    cache backend (used for dedup) is experiencing an infrastructure issue.
    """
    dedup_gate_acquired = False
    if dedup_key and dedup_timeout > 0:
        try:
            dedup_gate_acquired = bool(cache.add(dedup_key, "1", timeout=dedup_timeout))
            if not dedup_gate_acquired:
                return True
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            if logger:
                logger.warning(
                    "celery dispatch dedup cache unavailable: %s",
                    dedup_key,
                    exc_info=True,
                    extra={"degraded": True, "component": "celery_dedup_cache"},
                )

    ok: bool | None = None
    try:
        ok = safe_apply_async(
            task,
            args=args,
            kwargs=kwargs,
            countdown=countdown,
            logger=logger,
            log_message=log_message,
            raise_on_failure=raise_on_failure,
        )
        return ok
    finally:
        # Dispatch failures and unexpected errors should both release the dedup gate so retries are not suppressed.
        if dedup_gate_acquired and ok is not True:
            _rollback_dedup_gate(dedup_key, logger=logger)
