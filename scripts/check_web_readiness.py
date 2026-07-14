from __future__ import annotations

import os
import urllib.request
from collections.abc import Mapping

READINESS_URL = "http://127.0.0.1:8000/health/ready"


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_readiness_request(environ: Mapping[str, str] | None = None) -> urllib.request.Request:
    environment = os.environ if environ is None else environ
    host = environment.get("DJANGO_HEALTHCHECK_HOST", "").strip()
    if not host:
        allowed_hosts = environment.get("DJANGO_ALLOWED_HOSTS", "")
        host = next((value.strip() for value in allowed_hosts.split(",") if value.strip()), "")
    if not host:
        raise RuntimeError("DJANGO_HEALTHCHECK_HOST or DJANGO_ALLOWED_HOSTS must provide a healthcheck Host")

    headers = {"Host": host}
    if environment.get("DJANGO_SECURE_SSL_REDIRECT") == "1":
        headers["X-Forwarded-Proto"] = "https"
    return urllib.request.Request(READINESS_URL, headers=headers)


def main(environ: Mapping[str, str] | None = None) -> int:
    request = build_readiness_request(environ)
    opener = urllib.request.build_opener(NoRedirectHandler())
    with opener.open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Readiness endpoint returned non-success status {response.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
