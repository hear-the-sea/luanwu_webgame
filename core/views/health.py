from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Callable, Iterator
from typing import Any

from asgiref.sync import async_to_sync
from celery.exceptions import OperationalError as CeleryOperationalError
from celery.exceptions import TimeoutError as CeleryTimeoutError
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.utils import DatabaseError
from django.http import Http404, HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from kombu.exceptions import OperationalError as KombuOperationalError

from core.tasks import CELERY_BEAT_HEARTBEAT_CACHE_KEY, celery_health_ping
from core.utils.degradation import get_degradation_counts
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS, combine_infrastructure_exceptions
from core.utils.network import get_client_ip, is_trusted_proxy_ip
from core.utils.task_monitoring import get_degraded_counter, get_task_metrics
from core.views import health_support
from websocket.routing_status import get_websocket_routing_status

logger = logging.getLogger(__name__)

_DEGRADED_COMPONENTS = [
    "cache_lock_fail_closed",
    "local_lock_fallback",
    "login_security_degraded",
    "celery_dispatch_failed",
]
_READY_CACHE_KEY = "health:ready:payload:v1"


class HealthCheckFailure(RuntimeError):
    """Expected health check failure that should degrade readiness instead of bubbling."""


CHANNEL_LAYER_READY_EXCEPTIONS = combine_infrastructure_exceptions(HealthCheckFailure)
CELERY_BEAT_READY_EXCEPTIONS = combine_infrastructure_exceptions(
    HealthCheckFailure,
    infrastructure_exceptions=CACHE_INFRASTRUCTURE_EXCEPTIONS,
)
CELERY_READY_EXCEPTIONS = combine_infrastructure_exceptions(
    HealthCheckFailure,
    CeleryOperationalError,
    CeleryTimeoutError,
    KombuOperationalError,
)


def _maybe_debug_error(exc: Exception) -> str | None:
    if settings.DEBUG:
        return str(exc)
    return None


@require_GET
def health_live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _is_internal_request(request: HttpRequest) -> bool:
    remote_addr = request.META.get("REMOTE_ADDR", "")
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")

    if forwarded_for and not is_trusted_proxy_ip(remote_addr):
        return False

    client_ip = get_client_ip(request, trust_proxy=bool(forwarded_for))
    if client_ip == "unknown":
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _should_include_health_details() -> bool:
    return bool(getattr(settings, "HEALTH_CHECK_INCLUDE_DETAILS", False))


def _load_cached_ready_payload() -> tuple[dict[str, object], int] | None:
    try:
        return health_support.load_cached_ready_payload(
            cache_backend=cache,
            cache_key=_READY_CACHE_KEY,
            ttl_seconds=health_support.get_ready_cache_ttl(settings),
        )
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Health ready cached payload load failed: %s", exc, exc_info=True)
        return None


def _store_cached_ready_payload(payload: dict[str, object], status_code: int) -> None:
    try:
        health_support.store_cached_ready_payload(
            cache_backend=cache,
            cache_key=_READY_CACHE_KEY,
            payload=payload,
            status_code=status_code,
            ttl_seconds=health_support.get_ready_cache_ttl(settings),
        )
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Health ready cached payload store failed: %s", exc, exc_info=True)


def _iter_ready_checks() -> Iterator[tuple[str, bool, Callable[[], tuple[bool, str | None]]]]:
    """Yield enabled ready-check definitions in response order."""
    yield "db", True, _check_database_ready
    yield "cache", True, _check_cache_ready
    yield "channel_layer", bool(settings.HEALTH_CHECK_CHANNEL_LAYER), _check_channel_layer_ready
    yield "celery_broker", bool(settings.HEALTH_CHECK_CELERY_BROKER), _check_celery_broker_ready
    yield "celery_workers", bool(getattr(settings, "HEALTH_CHECK_CELERY_WORKERS", False)), _check_celery_workers_ready
    yield "celery_beat", bool(getattr(settings, "HEALTH_CHECK_CELERY_BEAT", False)), _check_celery_beat_ready
    yield "celery_roundtrip", bool(
        getattr(settings, "HEALTH_CHECK_CELERY_ROUNDTRIP", False)
    ), _check_celery_roundtrip_ready


def _collect_health_details() -> dict[str, object]:
    """Return optional detail sections for the ready payload."""
    return health_support.build_health_details(
        degraded_components=_DEGRADED_COMPONENTS,
        degradation_reader=get_degradation_counts,
        task_metrics_reader=get_task_metrics,
        degraded_counter_reader=get_degraded_counter,
    )


def _check_database_ready() -> tuple[bool, str | None]:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, None
    except DatabaseError as exc:
        return False, _maybe_debug_error(exc)


def _check_cache_ready() -> tuple[bool, str | None]:
    key = "health:ready:cache"
    marker = "1"
    try:
        cache.set(key, marker, timeout=5)
        cached_value = cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        return False, _maybe_debug_error(exc)
    finally:
        try:
            cache.delete(key)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS:
            pass
    if cached_value != marker:
        return False, _maybe_debug_error(RuntimeError("cache roundtrip failed"))
    return True, None


async def _channel_layer_roundtrip(channel_layer: Any, timeout_seconds: float) -> dict[str, str]:
    channel_name = await channel_layer.new_channel("health.ready")
    payload = {"type": "health.ready", "marker": "ok"}
    await channel_layer.send(channel_name, payload)
    try:
        received = await asyncio.wait_for(channel_layer.receive(channel_name), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise HealthCheckFailure(f"channel layer receive timed out after {timeout_seconds:.2f}s") from exc
    if not isinstance(received, dict) or received.get("marker") != payload["marker"]:
        raise HealthCheckFailure("channel layer roundtrip failed")
    return received


def _isolated_channel_layer_for_health_check(channel_layer: Any) -> tuple[Any, bool]:
    """Use a private Redis layer so WebSocket receive locks cannot starve readiness."""
    try:
        from channels_redis.core import RedisChannelLayer
    except ImportError:
        return channel_layer, False

    if not isinstance(channel_layer, RedisChannelLayer):
        return channel_layer, False

    configured_layer = getattr(settings, "CHANNEL_LAYERS", {}).get("default", {})
    layer_config = configured_layer.get("CONFIG", {}) if isinstance(configured_layer, dict) else {}
    if not isinstance(layer_config, dict):
        return channel_layer, False
    return RedisChannelLayer(**layer_config), True


def _check_channel_layer_ready() -> tuple[bool, str | None]:
    isolated_layer = False
    channel_layer = None
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            raise HealthCheckFailure("channel layer is unavailable")
        channel_layer, isolated_layer = _isolated_channel_layer_for_health_check(channel_layer)
        timeout_seconds = max(0.01, float(getattr(settings, "HEALTH_CHECK_CHANNEL_LAYER_TIMEOUT_SECONDS", 1.0)))
        async_to_sync(_channel_layer_roundtrip)(channel_layer, timeout_seconds)
        return True, None
    except CHANNEL_LAYER_READY_EXCEPTIONS as exc:
        return False, _maybe_debug_error(exc)
    finally:
        if isolated_layer and channel_layer is not None:
            close_pools = getattr(channel_layer, "close_pools", None)
            if close_pools is not None:
                try:
                    async_to_sync(close_pools)()
                except CHANNEL_LAYER_READY_EXCEPTIONS:
                    logger.warning("Health check channel layer pool cleanup failed", exc_info=True)


def _check_celery_broker_ready() -> tuple[bool, str | None]:
    try:
        from config.celery import app as celery_app

        with celery_app.connection_for_read() as connection:
            connection.connect()
        return True, None
    except CELERY_READY_EXCEPTIONS as exc:
        return False, _maybe_debug_error(exc)


def _check_celery_workers_ready() -> tuple[bool, str | None]:
    try:
        from config.celery import app as celery_app

        responses = celery_app.control.inspect(timeout=1.0).ping() or {}
        if not responses:
            raise HealthCheckFailure("no Celery workers responded to ping")
        return True, None
    except CELERY_READY_EXCEPTIONS as exc:
        return False, _maybe_debug_error(exc)


def _check_celery_beat_ready() -> tuple[bool, str | None]:
    max_age_seconds = max(60, int(getattr(settings, "HEALTH_CHECK_CELERY_BEAT_MAX_AGE_SECONDS", 180)))
    try:
        last_seen_raw = cache.get(CELERY_BEAT_HEARTBEAT_CACHE_KEY)
        if last_seen_raw is None:
            raise HealthCheckFailure("Celery beat heartbeat missing")

        last_seen = float(last_seen_raw)
        age_seconds = timezone.now().timestamp() - last_seen
        if age_seconds > max_age_seconds:
            raise HealthCheckFailure(f"Celery beat heartbeat stale: {int(age_seconds)}s old")
        return True, None
    except CELERY_BEAT_READY_EXCEPTIONS as exc:
        return False, _maybe_debug_error(exc)


def _check_celery_roundtrip_ready() -> tuple[bool, str | None]:
    timeout_seconds = max(0.5, float(getattr(settings, "HEALTH_CHECK_CELERY_ROUNDTRIP_TIMEOUT_SECONDS", 3.0)))
    async_result = None
    try:
        async_result = celery_health_ping.apply_async()
        response = async_result.get(timeout=timeout_seconds, disable_sync_subtasks=False)
        if response != "pong":
            raise HealthCheckFailure(f"unexpected Celery roundtrip payload: {response!r}")
        return True, None
    except CELERY_READY_EXCEPTIONS as exc:
        return False, _maybe_debug_error(exc)
    finally:
        if async_result is not None:
            try:
                async_result.forget()
            except CELERY_READY_EXCEPTIONS:
                pass


@require_GET
def health_ready(request: HttpRequest) -> JsonResponse:
    if getattr(settings, "HEALTH_CHECK_REQUIRE_INTERNAL", False) and not _is_internal_request(request):
        raise Http404()

    cached = _load_cached_ready_payload()
    if cached is not None:
        cached_payload, status_code = cached
        return JsonResponse(cached_payload, status=status_code)

    checks: dict[str, bool] = {}
    errors: dict[str, str] = {}

    for check_name, enabled, checker in _iter_ready_checks():
        if not enabled:
            continue
        ok, error = checker()
        health_support.apply_check_result(
            checks=checks,
            errors=errors,
            check_name=check_name,
            ok=ok,
            error=error,
        )

    websocket_routing_ok, websocket_routing_error = get_websocket_routing_status()
    if not websocket_routing_ok:
        checks["websocket_routing"] = False
        if settings.DEBUG and websocket_routing_error:
            errors["websocket_routing"] = websocket_routing_error

    ok = all(checks.values())
    payload: dict[str, object] = {"status": "ok" if ok else "error", "checks": checks}
    if errors:
        payload["errors"] = errors

    if _should_include_health_details():
        payload.update(_collect_health_details())

    status_code = 200 if ok else 503
    _store_cached_ready_payload(payload, status_code)
    return JsonResponse(payload, status=status_code)
