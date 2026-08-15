from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from django.http import HttpRequest

from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor
from gameplay.request_context import PREPARED_MANOR_REQUEST_ATTR, clear_prepared_manor, set_prepared_manor  # noqa: F401
from gameplay.services.manor.core import get_manor

EXPECTED_READ_PROJECTION_ERRORS = DATABASE_INFRASTRUCTURE_EXCEPTIONS
ProjectionResultT = TypeVar("ProjectionResultT")


@dataclass(frozen=True, slots=True)
class PreparedManorRead(Generic[ProjectionResultT]):
    """The manor and typed result produced by its read projection."""

    manor: Manor
    projection_result: ProjectionResultT | None
    projection_succeeded: bool


def _run_manor_read_projection(
    manor: Manor,
    *,
    project_fn: Callable[[Manor], ProjectionResultT],
    logger: logging.Logger,
    source: str,
    user_id: int | None = None,
    on_expected_failure: Callable[[Exception], None] | None = None,
) -> tuple[bool, ProjectionResultT | None]:
    try:
        return True, project_fn(manor)
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
        return False, None


def prepare_manor_for_read(
    manor: Manor,
    *,
    project_fn: Callable[[Manor], ProjectionResultT],
    logger: logging.Logger,
    source: str,
    user_id: int | None = None,
    on_expected_failure: Callable[[Exception], None] | None = None,
) -> bool:
    """Run manor read projection with consistent view-layer degradation semantics."""
    projection_succeeded, _projection_result = _run_manor_read_projection(
        manor,
        project_fn=project_fn,
        logger=logger,
        source=source,
        user_id=user_id,
        on_expected_failure=on_expected_failure,
    )
    return projection_succeeded


def get_prepared_manor_for_read_result(
    request: HttpRequest,
    *,
    project_fn: Callable[[Manor], ProjectionResultT],
    logger: logging.Logger,
    source: str,
    on_expected_failure: Callable[[Exception], None] | None = None,
) -> PreparedManorRead[ProjectionResultT]:
    """Load a manor, run its projection, and return the typed projection result."""
    clear_prepared_manor(request)
    manor = get_manor(request.user)
    projection_succeeded, projection_result = _run_manor_read_projection(
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
    return PreparedManorRead(
        manor=manor,
        projection_result=projection_result,
        projection_succeeded=projection_succeeded,
    )


def get_prepared_manor_for_read(
    request: HttpRequest,
    *,
    project_fn: Callable[[Manor], ProjectionResultT],
    logger: logging.Logger,
    source: str,
    on_expected_failure: Callable[[Exception], None] | None = None,
) -> Manor:
    """Load the current manor and run the standard read projection flow."""
    return get_prepared_manor_for_read_result(
        request,
        project_fn=project_fn,
        logger=logger,
        source=source,
        on_expected_failure=on_expected_failure,
    ).manor
