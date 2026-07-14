from __future__ import annotations

import ipaddress
from typing import Any, Iterable, Mapping

from django.conf import settings
from django.http import HttpRequest


def _is_trusted_proxy(remote_addr: str, trusted_proxy_ips: Iterable[str]) -> bool:
    if not remote_addr:
        return False

    try:
        remote_ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False

    for raw in trusted_proxy_ips:
        item = (raw or "").strip()
        if not item:
            continue
        try:
            if "/" in item:
                network = ipaddress.ip_network(item, strict=False)
                if remote_ip in network:
                    return True
            else:
                if remote_ip == ipaddress.ip_address(item):
                    return True
        except ValueError:
            continue

    return False


def is_trusted_proxy_ip(remote_addr: str) -> bool:
    trusted_proxy_ips = getattr(settings, "TRUSTED_PROXY_IPS", [])
    return _is_trusted_proxy(remote_addr, trusted_proxy_ips)


def _extract_client_ip_from_forwarded_chain(
    remote_addr: str, x_forwarded_for: str, trusted_proxy_ips: Iterable[str]
) -> str:
    chain: list[str] = []
    for part in (x_forwarded_for or "").split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        chain.append(candidate)

    if not chain:
        return remote_addr

    # Evaluate the hop chain from the server side backwards:
    # X-Forwarded-For entries ..., immediate client/proxy, REMOTE_ADDR.
    # Strip trusted proxies from the right; the first remaining hop is the client.
    chain.append(remote_addr)
    while len(chain) > 1 and _is_trusted_proxy(chain[-1], trusted_proxy_ips):
        chain.pop()

    return chain[-1] if chain else remote_addr


def get_client_ip(request: HttpRequest, *, trust_proxy: bool = False) -> str:
    """
    Get client IP with optional trusted-proxy support.

    When trust_proxy=True, X-Forwarded-For is used only if REMOTE_ADDR belongs
    to TRUSTED_PROXY_IPS.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "")
    if not remote_addr:
        return "unknown"

    if not trust_proxy:
        return remote_addr

    trusted_proxy_ips = getattr(settings, "TRUSTED_PROXY_IPS", [])
    if not _is_trusted_proxy(remote_addr, trusted_proxy_ips):
        return remote_addr

    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not x_forwarded_for:
        return remote_addr

    return _extract_client_ip_from_forwarded_chain(remote_addr, x_forwarded_for, trusted_proxy_ips)


def get_asgi_client_ip(scope: Mapping[str, Any], *, trust_proxy: bool = False) -> str:
    """Resolve an ASGI peer using the same trusted-proxy policy as HTTP requests."""
    client = scope.get("client")
    remote_addr = str(client[0]) if isinstance(client, (list, tuple)) and client else ""
    if not remote_addr:
        return "unknown"

    if not trust_proxy:
        return remote_addr

    trusted_proxy_ips = getattr(settings, "TRUSTED_PROXY_IPS", [])
    if not _is_trusted_proxy(remote_addr, trusted_proxy_ips):
        return remote_addr

    x_forwarded_for = ""
    for raw_name, raw_value in scope.get("headers") or []:
        if bytes(raw_name).lower() != b"x-forwarded-for":
            continue
        try:
            x_forwarded_for = bytes(raw_value).decode("latin-1")
        except (TypeError, ValueError, UnicodeError):
            x_forwarded_for = ""
        break

    if not x_forwarded_for:
        return remote_addr

    return _extract_client_ip_from_forwarded_chain(remote_addr, x_forwarded_for, trusted_proxy_ips)
