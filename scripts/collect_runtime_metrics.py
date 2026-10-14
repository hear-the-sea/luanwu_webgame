"""Collect bounded, credential-free host metrics without starting Django."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, timeout=12, stderr=subprocess.DEVNULL)


def disk_counters(text: str, device: str) -> dict[str, int]:
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 14 and fields[2] == device:
            values = list(map(int, fields[3:14]))
            return {
                "reads": values[0],
                "read_ms": values[3],
                "writes": values[4],
                "write_ms": values[7],
                "busy_ms": values[9],
                "weighted_ms": values[10],
            }
    raise ValueError("data device missing")


def disk_delta(current: dict[str, int], previous: dict[str, int], seconds: float) -> dict[str, float]:
    if seconds <= 0 or any(current[k] < previous.get(k, 0) for k in current):
        return {}
    delta = {k: current[k] - previous.get(k, current[k]) for k in current}
    operations = delta["reads"] + delta["writes"]
    return {
        "operations_per_second": round(operations / seconds, 2),
        "average_io_ms": round((delta["read_ms"] + delta["write_ms"]) / max(1, operations), 2),
        "busy_percent": round(min(100, delta["busy_ms"] / (seconds * 10)), 2),
        "average_queue_depth": round(delta["weighted_ms"] / (seconds * 1000), 2),
    }


def redis_info(container: str) -> dict[str, str]:
    # Read auth inside the existing container, never put credentials in argv.
    raw = run(["docker", "exec", container, "sh", "-c", 'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli INFO'])
    allowed = {
        "uptime_in_seconds",
        "used_memory",
        "maxmemory",
        "evicted_keys",
        "rejected_connections",
        "aof_delayed_fsync",
        "aof_enabled",
        "aof_last_write_status",
        "instantaneous_ops_per_sec",
        "connected_clients",
        "blocked_clients",
    }
    return {k: v.strip() for line in raw.splitlines() if ":" in line for k, v in [line.split(":", 1)] if k in allowed}


def request_summary(rows: list[dict]) -> dict:
    durations = defaultdict(list)
    statuses = Counter()
    ws = Counter()
    for row in rows:
        request = row.get("request", {})
        path = endpoint_path(request.get("uri", ""))
        status = str(row.get("status", 0))
        if path.startswith("/ws/"):
            ws[status] += 1
            continue  # WebSocket duration is connection lifetime.
        if path.startswith(("/static/", "/media/")):
            continue
        if path not in durations and len(durations) >= 200:
            path = "/other/"
        statuses[status] += 1
        durations[path].append(float(row.get("duration", 0)) * 1000)
    endpoints = {}
    for path, values in durations.items():
        ordered = sorted(values)
        n = len(values)
        endpoints[path] = {
            "count": n,
            "p95_ms": round(ordered[int((n - 1) * 0.95)], 1),
            "max_ms": round(ordered[-1], 1),
            "over_1s": sum(x >= 1000 for x in values),
        }
    return {"http_status": dict(statuses), "websocket_status": dict(ws), "endpoints": endpoints}


def endpoint_path(uri: str) -> str:
    """Remove both query credentials and the email-verification path token."""
    path = urlsplit(uri).path
    path = re.sub(r"(/verify-email/)[^/]+", r"\1:token", path)
    return re.sub(r"/\d+(?=/|$)", "/:id", path)[:140]


def access_metrics(log_path: str, cursors: dict, max_bytes: int) -> tuple[dict, dict]:
    """Read newly appended records; rotation tracked by inode, with a hard work cap."""
    paths = [Path(log_path), *Path(log_path).parent.glob(Path(log_path).name + ".[0-9]*")]
    paths = sorted((p for p in paths if p.exists() and not p.name.endswith(".gz")), key=lambda p: p.stat().st_mtime)
    rows = []
    new_cursors = {}
    remaining = max_bytes
    truncated = False
    for path in paths:
        stat = path.stat()
        key = str(stat.st_ino)
        # First run starts at EOF; old history must not be reported as current.
        offset = min(int(cursors.get(key, stat.st_size)), stat.st_size)
        if key not in cursors and cursors and stat.st_mtime >= time.time() - 180:
            offset = 0  # Newly rotated file since the previous run.
        with path.open("rb") as source:
            source.seek(offset)
            while remaining > 0:
                position = source.tell()
                line = source.readline(min(remaining, 1024 * 1024))
                if not line:
                    break
                if not line.endswith(b"\n"):
                    source.seek(position)
                    break
                remaining -= len(line)
                try:
                    outer = json.loads(line)
                    row = json.loads(outer["log"])
                    if row.get("logger", "").startswith("http.log.access"):
                        # Never retain headers, cookies, IPs or query values in
                        # memory across records; bound RSS under traffic growth.
                        rows.append(
                            {
                                "request": {"uri": endpoint_path(row.get("request", {}).get("uri", ""))},
                                "status": row.get("status", 0),
                                "duration": row.get("duration", 0),
                            }
                        )
                except (ValueError, KeyError, TypeError):
                    continue
            new_cursors[key] = source.tell()
        if new_cursors[key] < stat.st_size:
            truncated = True
    result = request_summary(rows)
    result.update({"complete": not truncated, "first_run": not bool(cursors), "bytes_read": max_bytes - remaining})
    return result, new_cursors


def warnings_for(snapshot: dict, previous: dict) -> list[str]:
    warnings = []
    if snapshot.get("memory_available_mb", 99999) < 1024:
        warnings.append("host_memory_headroom_low")
    for name, row in snapshot.get("containers", {}).items():
        if row.get("limit_bytes") and row.get("used_bytes", 0) / row["limit_bytes"] > 0.85:
            warnings.append(name + ":memory_over_85_percent")
        if row.get("status") != "running":
            warnings.append(name + ":not_running")
        if row.get("health") == "unhealthy":
            warnings.append(name + ":unhealthy")
        if row.get("restarts", 0) > previous.get("containers", {}).get(name, {}).get("restarts", 0):
            warnings.append(name + ":restarted")
    for name, info in snapshot.get("redis", {}).items():
        maximum = int(info.get("maxmemory", 0))
        if maximum and int(info.get("used_memory", 0)) / maximum > 0.8:
            warnings.append(name + ":dataset_over_80_percent")
        old = previous.get("redis", {}).get(name, {})
        if old and int(info.get("uptime_in_seconds", 0)) < int(old.get("uptime_in_seconds", 0)):
            warnings.append(name + ":instance_restarted")
        for counter in ("aof_delayed_fsync", "evicted_keys", "rejected_connections"):
            if old and int(info.get(counter, 0)) > int(old.get(counter, 0)):
                warnings.append(name + ":" + counter + "_increased")
    if snapshot.get("disk_interval", {}).get("average_io_ms", 0) > 25:
        warnings.append("data_disk_average_io_over_25ms")
    if not snapshot.get("access", {}).get("complete", True):
        warnings.append("access_metrics_backlog")
    for queue, count in snapshot.get("queues", {}).items():
        if count > (100 if queue.startswith("timer") else 20):
            warnings.append(queue + ":queue_backlog")
    for status, count in snapshot.get("access", {}).get("http_status", {}).items():
        if int(status) >= 500 and count:
            warnings.append("http_5xx_observed")
            break
    for path, row in snapshot.get("access", {}).get("endpoints", {}).items():
        if not path.startswith("/health/") and row["count"] >= 20 and row["p95_ms"] > 500:
            warnings.append("http_p95_over_500ms:" + path)
    if snapshot.get("access", {}).get("websocket_status", {}).get("403", 0) > 100:
        warnings.append("websocket_rejections_over_100")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="vdb")
    parser.add_argument("--project", default="code")
    args = parser.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_file = args.output_dir / "collector-state.json"
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    now = time.time()
    snapshot = {"timestamp": datetime.now(timezone.utc).isoformat(), "unix": now, "probe_errors": []}
    memory = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()}
    snapshot["memory_available_mb"] = memory["MemAvailable"] // 1024
    storage = shutil.disk_usage(args.output_dir)
    snapshot["metrics_disk_free_percent"] = round(storage.free * 100 / storage.total, 2)
    snapshot["disk_counters"] = disk_counters(Path("/proc/diskstats").read_text(), args.device)
    if previous.get("disk_counters"):
        snapshot["disk_interval"] = disk_delta(
            snapshot["disk_counters"], previous["disk_counters"], now - previous["unix"]
        )
    names = [
        f"{args.project}-{n}-1"
        for n in (
            "web",
            "worker",
            "worker_battle",
            "worker_timer",
            "worker_timer_scan",
            "worker_timer_safety",
            "worker_timer_maintenance",
            "beat",
            "db",
            "redis",
            "redis_runtime",
            "caddy",
        )
    ]
    available = set(run(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines())
    existing = [name for name in names if name in available]
    rows = json.loads(run(["docker", "inspect", *existing])) if existing else []
    for name in set(names) - available:
        snapshot["probe_errors"].append({"probe": name, "error_type": "container_missing"})
    cursors = previous.get("cursors", {})
    snapshot["containers"] = {}
    for row in rows:
        name = row["Name"].lstrip("/")
        group = Path("/sys/fs/cgroup/system.slice") / ("docker-" + row["Id"] + ".scope")
        used = int((group / "memory.current").read_text()) if (group / "memory.current").exists() else None
        snapshot["containers"][name] = {
            "status": row["State"]["Status"],
            "restarts": row["RestartCount"],
            "health": row["State"].get("Health", {}).get("Status"),
            "used_bytes": used or 0,
            "memory_measured": used is not None,
            "limit_bytes": row["HostConfig"]["Memory"],
        }
        if name == args.project + "-caddy-1":
            snapshot["access"], cursors = access_metrics(row["LogPath"], previous.get("cursors", {}), 64 * 1024 * 1024)
    snapshot["redis"] = {}
    for name in (args.project + "-redis-1", args.project + "-redis_runtime-1"):
        try:
            snapshot["redis"][name] = redis_info(name)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            snapshot["probe_errors"].append({"probe": name, "error_type": type(exc).__name__})
    try:
        # Read all priority buckets in one command; the persistent broker stays DB0.
        script = "local r={} for _,q in ipairs({'default','battle','timer','timer_scan','timer_safety','timer_maintenance'}) do local n=0 for _,s in ipairs({'','\\006\\0223','\\006\\0226','\\006\\0229'}) do n=n+redis.call('LLEN',q..s) end table.insert(r,q) table.insert(r,n) end return r"
        command = 'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli --raw -n 0 EVAL "$1" 0'
        values = run(["docker", "exec", args.project + "-redis-1", "sh", "-c", command, "monitor", script]).splitlines()
        snapshot["queues"] = {values[i]: int(values[i + 1]) for i in range(0, len(values), 2)}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        snapshot["probe_errors"].append({"probe": "queues", "error_type": type(exc).__name__})
    snapshot["warnings"] = warnings_for(snapshot, previous)
    if snapshot["metrics_disk_free_percent"] < 20:
        snapshot["warnings"].append("metrics_disk_headroom_low")
    snapshot["cursors"] = cursors
    temporary = state_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot))
    temporary.replace(state_file)
    entry = {k: v for k, v in snapshot.items() if k != "cursors"}
    filename = args.output_dir / (datetime.now(timezone.utc).strftime("metrics-%Y-%m-%d") + ".jsonl")
    with filename.open("a") as output:
        output.write(json.dumps(entry) + "\n")
    # Only this collector's dated files are eligible for retention cleanup.
    for old in args.output_dir.glob("metrics-????-??-??.jsonl"):
        try:
            dated = datetime.strptime(old.stem, "metrics-%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - dated.timestamp() > 15 * 86400:
            old.unlink()
    print(json.dumps({"status": "ok" if not snapshot["probe_errors"] else "partial", "warnings": snapshot["warnings"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
