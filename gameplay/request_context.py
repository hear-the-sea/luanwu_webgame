"""Request attributes shared by gameplay view and page-context layers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpRequest

if TYPE_CHECKING:
    from gameplay.models import Manor

PREPARED_MANOR_REQUEST_ATTR = "_prepared_manor_for_read"


def set_prepared_manor(request: HttpRequest, manor: Manor) -> None:
    """Store the projected manor for the lifetime of the current request."""
    setattr(request, PREPARED_MANOR_REQUEST_ATTR, manor)


def get_prepared_manor(request: HttpRequest, *, user_id: int | None) -> Manor | None:
    """Return the request-local manor only when it belongs to the current user."""
    if user_id is None:
        return None

    manor = getattr(request, PREPARED_MANOR_REQUEST_ATTR, None)
    if manor is not None and getattr(manor, "pk", None) and getattr(manor, "user_id", None) == user_id:
        return manor
    return None


def clear_prepared_manor(request: HttpRequest) -> None:
    """Invalidate the request-local manor after a write changes its authoritative state."""
    request.__dict__.pop(PREPARED_MANOR_REQUEST_ATTR, None)
