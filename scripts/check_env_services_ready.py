from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

DEFAULT_PROBE_TIMEOUT_SECONDS = 3


@dataclass(frozen=True)
class ServiceProbe:
    name: str
    endpoint: str
    command: list[str]
    env_overrides: dict[str, str]
    timeout_seconds: int


def _coerce_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def build_mysql_probe(env: Mapping[str, str] | None = None) -> ServiceProbe:
    resolved_env = _coerce_env(env)
    host = str(resolved_env.get("DJANGO_DB_HOST", "")).strip()
    port = str(resolved_env.get("DJANGO_DB_PORT", "")).strip()
    user = str(resolved_env.get("DJANGO_DB_USER", "")).strip()
    password = str(resolved_env.get("DJANGO_DB_PASSWORD", ""))

    command = ["mysqladmin", "ping"]
    if host:
        command.extend(["-h", host])
    if port:
        command.extend(["-P", port])
    if user:
        command.extend(["-u", user])

    endpoint = "local mysql socket"
    if host and port:
        endpoint = f"{host}:{port}"
    elif host:
        endpoint = host
    elif port:
        endpoint = f"localhost:{port}"

    env_overrides: dict[str, str] = {}
    if password:
        env_overrides["MYSQL_PWD"] = password

    return ServiceProbe(
        name="mysql",
        endpoint=endpoint,
        command=command,
        env_overrides=env_overrides,
        timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
    )


def build_redis_probe(env: Mapping[str, str] | None = None) -> ServiceProbe:
    resolved_env = _coerce_env(env)
    redis_url = str(
        resolved_env.get("REDIS_CACHE_URL") or resolved_env.get("REDIS_URL") or "redis://127.0.0.1:6379/2"
    ).strip()
    parsed = urlparse(redis_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    database = parsed.path.lstrip("/") or "0"
    command = ["redis-cli", "-h", host, "-p", str(port), "-n", database]
    if parsed.scheme == "rediss":
        command.append("--tls")
    if parsed.username:
        command.extend(["--user", parsed.username])
    command.append("ping")

    env_overrides: dict[str, str] = {}
    if parsed.password:
        env_overrides["REDISCLI_AUTH"] = parsed.password

    return ServiceProbe(
        name="redis",
        endpoint=f"{host}:{port}",
        command=command,
        env_overrides=env_overrides,
        timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
    )


def run_probe(
    probe: ServiceProbe,
    *,
    env: Mapping[str, str] | None = None,
    runner=subprocess.run,
) -> str | None:
    resolved_env = dict(_coerce_env(env))
    resolved_env.update(probe.env_overrides)
    try:
        result = runner(
            probe.command,
            env=resolved_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=probe.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return f"{probe.name} preflight timed out at {probe.endpoint} after {probe.timeout_seconds}s"
    except OSError as exc:
        return f"{probe.name} preflight failed at {probe.endpoint}: {exc}"

    if result.returncode == 0:
        return None

    details = (result.stderr or result.stdout or "").strip() or f"exit={result.returncode}"
    return f"{probe.name} preflight failed at {probe.endpoint}: {details}"


def check_env_services_ready(
    env: Mapping[str, str] | None = None,
    *,
    runner=subprocess.run,
) -> list[str]:
    resolved_env = _coerce_env(env)
    failures: list[str] = []
    for probe in (build_mysql_probe(resolved_env), build_redis_probe(resolved_env)):
        failure = run_probe(probe, env=resolved_env, runner=runner)
        if failure:
            failures.append(failure)
    return failures


def format_real_service_start_hint() -> str:
    return "\n".join(
        [
            "Start MySQL and Redis with `make test-real-services-up`, then run:",
            "  `DJANGO_TEST_USE_ENV_SERVICES=1 make test-real-services`",
            "When finished, stop the services with `make test-real-services-down`.",
        ]
    )


def main() -> int:
    failures = check_env_services_ready()
    if not failures:
        print("real-services preflight passed: mysql and redis are reachable")
        return 0

    print("real-services preflight failed:", file=sys.stderr)
    for failure in failures:
        print(f"  - {failure}", file=sys.stderr)
    print(format_real_service_start_hint(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
