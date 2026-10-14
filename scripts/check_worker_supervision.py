#!/usr/bin/env python3
"""Validate worker supervision using private Redis and disposable prefork workers.

Checks that an idle worker with cancelled consumption exits without losing its
queued message, and that running slow tasks survive the short watchdog timeout.
Requires redis-server on PATH; never connects to an existing Redis service.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from celery import Celery, bootsteps
from redis import Redis
from redis.exceptions import ConnectionError, TimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def stop(process: subprocess.Popen | None, timeout: float = 30) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Each fixture owns its process group, including prefork children.
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def wait_file(path: Path, process: subprocess.Popen, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return
        if process.poll() is not None:
            raise RuntimeError(f"Worker exited before {path.name}: {process.returncode}")
        time.sleep(0.1)
    raise RuntimeError(f"Worker did not create {path.name} within {timeout}s")


def run_worker(mode: str, port: int, directory: Path) -> None:
    from core.worker_liveness import register_worker_supervision

    app = Celery("watchdog_isolated", broker=f"redis://127.0.0.1:{port}/0")
    app.conf.update(
        worker_prefetch_multiplier=1,
        worker_stall_watchdog_startup_grace=1,
        worker_stall_watchdog_timeout=3,
        worker_stall_watchdog_interval=1,
        worker_stall_watchdog_failures=2,
        worker_stall_watchdog_shutdown_grace=2,
    )
    register_worker_supervision(app)

    @app.task(name="isolated.slow", ignore_result=True)
    def slow():
        (directory / "task_started").write_text("started")
        time.sleep(9)

    class Fixture(bootsteps.StartStopStep):
        requires = ("core.worker_liveness:RedisConsumerSupervisor",)

        def start(self, consumer):
            def ready():
                if mode == "stall":
                    consumer.task_consumer.cancel()
                pool = consumer.pool._pool
                (directory / f"{mode}_ready.json").write_text(
                    json.dumps(
                        {
                            "class": type(pool).__name__,
                            "has_cache": hasattr(pool, "_cache"),
                            "cache_size": len(pool._cache),
                        }
                    )
                )

            consumer.timer.call_after(2, ready)

    app.steps["consumer"].add(Fixture)
    app.worker_main(
        [
            "worker",
            "-Q",
            "timer",
            "-c",
            "1",
            "--pool=prefork",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "-l",
            "INFO",
        ]
    )


def run_checks() -> dict:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = None
    workers = []
    publisher = Celery("publisher", broker=f"redis://127.0.0.1:{port}/0")
    client = Redis(host="127.0.0.1", port=port, socket_timeout=2, socket_connect_timeout=2)
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="worker-supervision-") as temporary:
        directory = Path(temporary)
        logs = []
        try:
            with (directory / "redis.log").open("w") as redis_log:
                server = subprocess.Popen(
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
                        str(directory),
                    ],
                    start_new_session=True,
                    stdout=redis_log,
                    stderr=subprocess.STDOUT,
                )
            deadline = time.monotonic() + 10
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"Disposable Redis exited: {server.returncode}")
                try:
                    if client.ping():
                        break
                except (ConnectionError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Disposable Redis startup timed out") from None
                time.sleep(0.1)

            def start_worker(mode: str) -> subprocess.Popen:
                log_path = directory / f"{mode}.log"
                logs.append(log_path)
                with log_path.open("w") as output:
                    worker = subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--worker-mode",
                            mode,
                            "--worker-port",
                            str(port),
                            "--worker-directory",
                            str(directory),
                        ],
                        start_new_session=True,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "C_FORCE_ROOT": "1"},
                    )
                workers.append(worker)
                wait_file(directory / f"{mode}_ready.json", worker)
                return worker

            stalled = start_worker("stall")
            publisher.send_task("isolated.slow", queue="timer")
            stalled.wait(timeout=20)
            if client.llen("timer") != 1:
                raise RuntimeError("Stalled worker lost the queued task")
            if "requesting worker restart" not in (directory / "stall.log").read_text():
                raise RuntimeError("Stalled worker did not request supervised restart")
            evidence["stalled_worker"] = {
                "exit_code": stalled.returncode,
                "queued_task_retained": True,
                "pool": json.loads((directory / "stall_ready.json").read_text()),
            }
            # This Redis process belongs exclusively to the test.
            client.flushdb()
            slow = start_worker("slow")
            for _ in range(3):
                publisher.send_task("isolated.slow", queue="timer")
            wait_file(directory / "task_started", slow)
            time.sleep(7)
            if slow.poll() is not None:
                raise RuntimeError("Long-running task caused an incorrect worker restart")
            if "requesting worker restart" in (directory / "slow.log").read_text():
                raise RuntimeError("Long-running task triggered the watchdog")
            evidence["long_task"] = {"survived_short_watchdog_timeout": True, "queued_backlog": client.llen("timer")}
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ConnectionError, TimeoutError):
            for log_path in [directory / "redis.log", *logs]:
                if log_path.exists():
                    print(f"{log_path.name}:\n{log_path.read_text()[-15000:]}", file=sys.stderr)
            raise
        finally:
            try:
                for worker in workers:
                    stop(worker)
            finally:
                stop(server, timeout=5)
                publisher.close()
                client.close()
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-mode", choices=("stall", "slow"), help=argparse.SUPPRESS)
    parser.add_argument("--worker-port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_mode:
        if args.worker_port is None or args.worker_directory is None:
            parser.error("worker mode requires --worker-port and --worker-directory")
        run_worker(args.worker_mode, args.worker_port, args.worker_directory)
    else:
        print(json.dumps({"status": "passed", **run_checks()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
