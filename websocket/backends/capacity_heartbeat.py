"""Shared WebSocket capacity heartbeat operations."""

from __future__ import annotations

from core.utils.infrastructure import INFRASTRUCTURE_EXCEPTIONS
from websocket.exceptions import WebSocketConnectionLimitUnavailable

from .connection_limiter import REFRESH_CONNECTION_SLOT_SCRIPT, _connection_key, refresh_connection_slot
from .ip_capacity import REFRESH_IP_CAPACITY_SCRIPT, _capacity_keys, refresh_ip_capacity_slot
from .worker_lease import encode_worker_owned_member


def refresh_connection_and_ip_capacity_slots(
    redis,
    *,
    user_id: int,
    client_ip: str,
    worker_id: str,
    user_connection_id: str,
    ip_connection_id: str,
    user_ttl_seconds: int,
    ip_ttl_seconds: int,
) -> tuple[bool, bool]:
    """Refresh user and IP capacity leases using their independently acquired members."""
    user_ttl = max(2, int(user_ttl_seconds))
    ip_ttl = max(30, int(ip_ttl_seconds))
    user_key = _connection_key(user_id)
    user_member = encode_worker_owned_member(worker_id, user_connection_id)
    ip_key, _throttle_key = _capacity_keys(client_ip)
    ip_member = encode_worker_owned_member(worker_id, ip_connection_id)

    try:
        pipeline_factory = getattr(redis, "pipeline", None)
        if not callable(pipeline_factory):
            return (
                refresh_connection_slot(
                    redis,
                    user_id=user_id,
                    worker_id=worker_id,
                    connection_id=user_connection_id,
                    ttl_seconds=user_ttl,
                ),
                refresh_ip_capacity_slot(
                    redis,
                    client_ip=client_ip,
                    worker_id=worker_id,
                    connection_id=ip_connection_id,
                    ttl_seconds=ip_ttl,
                ),
            )

        pipeline = pipeline_factory(transaction=True)
        pipeline.eval(
            REFRESH_CONNECTION_SLOT_SCRIPT,
            1,
            user_key,
            user_member,
            str(user_ttl),
            str(user_ttl * 2),
        )
        pipeline.eval(
            REFRESH_IP_CAPACITY_SCRIPT,
            1,
            ip_key,
            ip_member,
            str(ip_ttl),
        )
        results = list(pipeline.execute() or ())
        if len(results) != 2:
            raise ValueError(f"Unexpected WebSocket capacity heartbeat result: {results!r}")
        return bool(int(results[0] or 0)), bool(int(results[1] or 0))
    except INFRASTRUCTURE_EXCEPTIONS as exc:
        raise WebSocketConnectionLimitUnavailable("WebSocket capacity heartbeat unavailable") from exc


__all__ = ["refresh_connection_and_ip_capacity_slots"]
