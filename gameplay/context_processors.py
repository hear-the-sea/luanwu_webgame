from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError

import gameplay.selectors.sidebar as sidebar_selector
import gameplay.selectors.stats as stats_selector
from gameplay.request_context import get_prepared_manor
from gameplay.services.action_points import ACTION_POINTS_MAX
from gameplay.services.utils.messages import unread_message_count

logger = logging.getLogger(__name__)

DEFAULT_PROTECTION_STATUS = {"is_protected": False, "type_display": "", "remaining_display": ""}
_NOTIFICATIONS_REQUEST_CACHE_ATTR = "_notifications_context_cache"


def _build_default_context() -> dict[str, Any]:
    return {
        "message_unread_count": 0,
        "online_user_count": 0,
        "total_user_count": 0,
        "header_protection_status": DEFAULT_PROTECTION_STATUS.copy(),
        "sidebar_action_points_label": f"0/{ACTION_POINTS_MAX}",
        "sidebar_grain_quantity": 0,
    }


def _clone_notifications_context(context: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(context)
    protection_status = cloned.get("header_protection_status")
    if isinstance(protection_status, dict):
        cloned["header_protection_status"] = dict(protection_status)
    return cloned


def _should_load_global_stats(request) -> bool:
    if request.headers.get("x-partial-navigation") == "1":
        return True
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return False
    accept = request.headers.get("accept", "")
    if accept and "text/html" not in accept and "application/xhtml+xml" not in accept:
        return False
    return True


def _should_include_home_sidebar(request) -> bool:
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match is not None and getattr(resolver_match, "url_name", None) == "home":
        return True
    return getattr(request, "path", "") == "/"


def _populate_authenticated_context(context: dict[str, Any], request) -> None:
    try:
        manor = get_prepared_manor(request, user_id=getattr(request.user, "id", None)) or request.user.manor
    except (ObjectDoesNotExist, DatabaseError):
        logger.warning("Failed to resolve manor for sidebar context", exc_info=True)
        return

    try:
        context["message_unread_count"] = unread_message_count(manor)
    except DatabaseError:
        logger.warning("Failed to load unread message count", exc_info=True)

    try:
        from gameplay.services.raid import get_protection_status

        protection_status = get_protection_status(manor)
        if isinstance(protection_status, dict):
            context["header_protection_status"] = protection_status
    except DatabaseError:
        logger.warning("Failed to load protection status", exc_info=True)

    try:
        from gameplay.services.inventory.core import get_warehouse_grain_quantity

        context["sidebar_grain_quantity"] = get_warehouse_grain_quantity(manor)
    except DatabaseError:
        logger.warning("Failed to load warehouse grain quantity", exc_info=True)

    if not _should_include_home_sidebar(request):
        return

    context["sidebar_prestige"] = manor.prestige
    sidebar_action_points = sidebar_selector.load_sidebar_action_points(manor)
    context["sidebar_action_points"] = sidebar_action_points
    context["sidebar_action_points_label"] = f"{sidebar_action_points}/{ACTION_POINTS_MAX}"
    context["sidebar_current_contribution_label"] = _resolve_sidebar_current_contribution_label(request)

    try:
        context["sidebar_rank"] = sidebar_selector.load_sidebar_rank(manor)
    except DatabaseError:
        logger.warning("Failed to load sidebar rank", exc_info=True)


def _resolve_sidebar_current_contribution_label(request) -> str:
    try:
        membership = request.user.guild_membership
    except ObjectDoesNotExist:
        return "未加入帮会"
    except DatabaseError:
        logger.warning("Failed to load guild membership for sidebar context", exc_info=True)
        return "暂不可用"

    if not getattr(membership, "is_active", False):
        return "未加入帮会"
    return str(membership.current_contribution)


def notifications(request):
    """
    Provide unread message count and user statistics to every template.

    The notification badge in the navigation bar depends on this context value.
    Also provides online_user_count and total_user_count (excluding staff/superusers).

    Performance optimizations:
    - User counts are cached for 5 minutes
    - Online count uses Redis atomic SET operations
    - Sidebar raid/scout data cached for 10 seconds per user
    - Player rank cached for 30 seconds per user
    """
    cached_context = getattr(request, _NOTIFICATIONS_REQUEST_CACHE_ATTR, None)
    if isinstance(cached_context, dict):
        return _clone_notifications_context(cached_context)

    context = _build_default_context()
    if _should_load_global_stats(request):
        context["total_user_count"] = stats_selector.load_total_user_count()
        context["online_user_count"] = stats_selector.load_online_user_count()

    if not request.user.is_authenticated:
        setattr(request, _NOTIFICATIONS_REQUEST_CACHE_ATTR, _clone_notifications_context(context))
        return context

    _populate_authenticated_context(context, request)
    setattr(request, _NOTIFICATIONS_REQUEST_CACHE_ATTR, _clone_notifications_context(context))
    return context
