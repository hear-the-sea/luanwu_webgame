#!/usr/bin/env python3
"""Exercise Celery reconnects against a disposable loopback Redis process.

Run with the production dependency lock installed. Requires redis-server on PATH.
No project settings, production credentials, or existing Redis service are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

from celery import Celery
from redis import Redis


def make_app(port: int) -> Celery:
    app = Celery("recovery_probe", broker=f"redis://127.0.0.1:{port}/0", backend=f"redis://127.0.0.1:{port}/1")
    app.conf.update(
        broker_connection_retry_on_startup=True,
        broker_connection_retry=True,
        broker_connection_max_retries=None,
        broker_transport_options={"socket_timeout": 2, "socket_connect_timeout": 2},
        task_default_queue="isolated_recovery_probe",
        worker_prefetch_multiplier=1,
    )

    @app.task(name="recovery_probe.echo")
    def echo(value: str) -> str:
        return value

    return app


def wait_ready(port: int, process: subprocess.Popen, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    client = Redis(host="127.0.0.1", port=port, socket_timeout=1)
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Redis exited: {process.returncode}")
            try:
                if client.ping():
                    return
            except OSError:
                pass
            except Exception as exc:
                from redis.exceptions import ConnectionError

                if not isinstance(exc, ConnectionError):
                    raise
            time.sleep(0.1)
        raise RuntimeError("Disposable Redis did not become ready")
    finally:
        client.close()


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def channel_probe(port: int) -> None:
    from channels_redis.core import RedisChannelLayer

    layer = RedisChannelLayer(hosts=[f"redis://127.0.0.1:{port}/2"])
    try:
        channel = await layer.new_channel()
        await layer.send(channel, {"type": "recovery.test", "value": "ok"})
        message = await asyncio.wait_for(layer.receive(channel), timeout=5)
        if message["value"] != "ok":
            raise RuntimeError("Channels Redis roundtrip failed")
    finally:
        await layer.close_pools()


def cache_probe(port: int) -> None:
    from django.conf import settings
    from django.core.cache import caches

    settings.configure(
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": f"redis://127.0.0.1:{port}/3",
                "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
            }
        }
    )
    cache = caches["default"]
    cache.set("isolated_probe", 3, timeout=30)
    if cache.incr("isolated_probe") != 4 or cache.get("isolated_probe") != 4:
        raise RuntimeError("Django Redis cache roundtrip failed")
    cache.delete("isolated_probe")
    cache.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--worker-port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_port is not None:
        make_app(args.worker_port).worker_main(
            [
                "worker",
                "--loglevel=INFO",
                "--concurrency=1",
                "--pool=prefork",
                "--without-gossip",
                "--without-mingle",
                "--hostname=isolated_recovery@%h",
            ]
        )
        return 0
    if args.cycles < 1:
        parser.error("--cycles must be positive")

    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    redis_process = worker = None
    app = make_app(port)
    timings = []
    with tempfile.TemporaryDirectory(prefix="game-redis-recovery-") as directory:
        log_path = Path(directory) / "worker.log"
        with log_path.open("w+") as log:

            def start_redis() -> subprocess.Popen:
                process = subprocess.Popen(
                    [
                        "redis-server",
                        "--bind",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--save",
                        "",
                        "--appendonly",
                        "no",
                        "--dir",
                        directory,
                    ],
                    stdout=log,
                    stderr=log,
                )
                wait_ready(port, process)
                return process

            def run_task(value: str) -> None:
                result = app.send_task("recovery_probe.echo", args=[value])
                if result.get(timeout=45, interval=0.2) != value:
                    raise RuntimeError("Worker task result mismatch")
                result.forget()

            try:
                redis_process = start_redis()
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker-port",
                        str(port),
                    ],
                    stdout=log,
                    stderr=log,
                    env={**os.environ, "C_FORCE_ROOT": "1"},
                )
                run_task("before_disconnect")
                for cycle in range(args.cycles):
                    stop(redis_process)
                    time.sleep(2)
                    redis_process = start_redis()
                    started = time.monotonic()
                    run_task(f"after_disconnect_{cycle}")
                    timings.append(round(time.monotonic() - started, 3))
                    if worker.poll() is not None:
                        raise RuntimeError("Worker exited during broker reconnect")
                asyncio.run(channel_probe(port))
                cache_probe(port)
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "disconnect_cycles": args.cycles,
                            "recovery_seconds": timings,
                            "channels": "passed",
                            "django_cache": "passed",
                            "versions": {
                                name: metadata.version(name)
                                for name in ("celery", "kombu", "redis", "channels-redis", "django-redis")
                            },
                        },
                        indent=2,
                    )
                )
            except Exception:
                log.flush()
                print(log_path.read_text()[-20000:], file=sys.stderr)
                raise
            finally:
                stop(worker)
                stop(redis_process)
                app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
