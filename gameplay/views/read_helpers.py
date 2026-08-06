from __future__ import annotations

import logging
from collections.abc import Callable

from django.http import HttpRequest

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.request_context import PREPARED_MANOR_REQUEST_ATTR, clear_prepared_manor, set_prepared_manor  # noqa: F401
from gameplay.services.manor.core import get_manor

EXPECTED_READ_PROJECTION_ERRORS = DATABASE_INFRASTRUCTURE_EXCEPTIONS


def prepare_manor_for_read(
    manor: Manor,
    *,
    project_fn: Callable[[Manor], None],
    logger: logging.Logger,
    source: str,
    user_id: int | None = None,
    on_expected_failure: Callable[[Exception], None] | None = None,
) -> bool:
    """Run manor read projection with consistent view-layer degradation semantics."""
    try:
        project_fn(manor)
        return True
    except EXPECTED_READ_PROJECTION_ERRORS as exc:
        logger.warning(
            "Manor read projection failed: source=%s manor_id=%s user_id=%s error=%s",
            source,
            getattr(manor, "id", None),
            user_id,
            exc,
            exc_info=True,
        )
        if on_expected_failure is not None:
            on_expected_failure(exc)
        return False


def get_prepared_manor_for_read(
    request: HttpRequest,
    *,
    project_fn: Callable[[Manor], None],
    logger: logging.Logger,
    source: str,
    on_expected_failure: Callable[[Exception], None] | None = None,
) -> Manor:
    """Load the current manor and run the standard read projection flow."""
    clear_prepared_manor(request)
    manor = get_manor(request.user)
    projection_succeeded = prepare_manor_for_read(
        manor,
        project_fn=project_fn,
        logger=logger,
        source=source,
        user_id=getattr(request.user, "id", None),
        on_expected_failure=on_expected_failure,
    )
    if projection_succeeded:
        set_prepared_manor(request, manor)
    else:
        clear_prepared_manor(request)
    return manor
