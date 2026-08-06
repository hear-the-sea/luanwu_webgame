"""Typed request-scope contract shared by WebSocket capacity layers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from typing import TypedDict, cast

CapacityRefreshPair = Callable[[int, str, str, int], Awaitable[tuple[bool, bool]]]

WEBSOCKET_IP_CAPACITY_STATE_KEY = "_websocket_ip_capacity_state"


class WebSocketIPCapacityState(TypedDict):
    """State owned by the IP middleware and optionally promoted by session guard."""

    managed_by_session_guard: bool
    ip_connection_id: str
    refresh_pair: CapacityRefreshPair


def set_websocket_ip_capacity_state(
    scope: MutableMapping[str, object],
    state: WebSocketIPCapacityState,
) -> None:
    """Attach the validated capacity state to an ASGI scope."""
    scope[WEBSOCKET_IP_CAPACITY_STATE_KEY] = state


def get_websocket_ip_capacity_state(scope: Mapping[str, object]) -> WebSocketIPCapacityState | None:
    """Read capacity state from an untyped ASGI scope without trusting malformed data."""
    raw_state = scope.get(WEBSOCKET_IP_CAPACITY_STATE_KEY)
    if not isinstance(raw_state, dict):
        return None
    if not isinstance(raw_state.get("managed_by_session_guard"), bool):
        return None
    if not isinstance(raw_state.get("ip_connection_id"), str):
        return None
    if not callable(raw_state.get("refresh_pair")):
        return None
    return cast(WebSocketIPCapacityState, raw_state)
