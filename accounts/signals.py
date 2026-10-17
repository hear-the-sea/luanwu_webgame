from __future__ import annotations

import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from accounts.utils import purge_other_sessions
from core.utils.degradation import SESSION_SYNC_FAILURE, record_degradation
from core.utils.infrastructure import DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)


@receiver(user_logged_in)
def grant_custom_guest_card_on_login(sender, request, user, **kwargs):
    from accounts.custom_guest_grant import grant_custom_guest_card_on_entry

    grant_custom_guest_card_on_entry(user)


def _record_session_sync_failure(user_id: int, detail: str) -> None:
    record_degradation(
        SESSION_SYNC_FAILURE,
        component="sync_active_session_on_login",
        detail=detail,
        user_id=user_id,
    )


@receiver(user_logged_in)
def sync_active_session_on_login(sender, request, user, **kwargs):
    del sender, kwargs
    try:
        request.session.save()
        success = purge_other_sessions(user.id, request.session.session_key)
        if not success:
            logger.warning(
                "Single-session enforcement may have failed for user %s",
                user.id,
                extra={"user_id": user.id, "degraded": True},
            )
            _record_session_sync_failure(user.id, "purge_other_sessions returned False")
    except DATABASE_CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Failed to sync active session on login for user %s", user.id, exc_info=True)
        _record_session_sync_failure(user.id, f"{type(exc).__name__}: {exc}")
