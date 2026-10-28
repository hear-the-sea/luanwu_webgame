import hashlib
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

P = Path(__file__).resolve().parent
M = json.loads((P / "release-manifest.json").read_text())
assert json.loads((P / "phase.json").read_text())["phase"] == "complete"
V = json.loads((P / "verification.json").read_text())
S = [
    "web",
    "worker",
    "worker_battle",
    "worker_timer",
    "worker_timer_scan",
    "worker_timer_safety",
    "worker_timer_maintenance",
    "beat",
]
report = {
    "status": "verifying",
    "site": "https://luanwu.top",
    "release": str(P),
    "verified_at_utc": datetime.now(timezone.utc).isoformat(),
    "services": {},
}
for s in S:
    info = json.loads(subprocess.check_output(["docker", "inspect", "code-" + s + "-1"]))[0]
    assert info["Config"]["Image"] == M["image"] and info["State"]["Running"]
    assert not info["State"]["OOMKilled"]
    report["services"][s] = {
        "image": info["Config"]["Image"],
        "running": True,
        "health": info["State"].get("Health", {}).get("Status"),
        "restart_count": info["RestartCount"],
        "memory": info["HostConfig"]["Memory"],
    }
assert report["services"]["web"]["health"] == "healthy"
subprocess.run(
    ["docker", "exec", "code-web-1", "python", "scripts/check_web_readiness.py"],
    check=True,
    capture_output=True,
    timeout=30,
)
ping = subprocess.run(
    ["docker", "exec", "code-web-1", "celery", "-A", "config", "inspect", "ping", "--timeout", "8", "--json"],
    capture_output=True,
    text=True,
    timeout=30,
)
assert ping.returncode == 0
workers = json.loads(ping.stdout)
assert len(workers) == 6 and all(x.get("ok") == "pong" for x in workers.values())
report["worker_responses"] = len(workers)
report["public_http"] = {}
for path in list(dict.fromkeys(["/health/live", "/accounts/login/"] + V["static_urls"] + V["portrait_urls"])):
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                "https://luanwu.top" + path, headers={"User-Agent": "Webgame-release-verification"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                assert response.status == 200
                record = {"status": 200, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)
    if path.startswith(("/static/", "/media/")):
        prefix = "/static/" if path.startswith("/static/") else "/media/"
        root = Path("/root/code/runtime/staticfiles" if prefix == "/static/" else "/root/code/runtime/media")
        assert record["sha256"] == hashlib.sha256((root / path.removeprefix(prefix)).read_bytes()).hexdigest(), path
    report["public_http"][path] = record
report.update(
    {
        "status": "deployed_and_verified",
        "verification": V,
        "changed_files": M["changed_files"],
        "avatar_changes": M["avatar_changes"],
        "archive_sha256": M["archive_sha256"],
        "offline_validation": json.loads((P / "offline-verification.json").read_text()),
        "previous_image": M["expected_base_image"],
        "backups": {
            "database": "database-before.sql.gz",
            "templates_at_cutover": "database-at-cutover.sql.gz",
            "media_static": "media-static-before.tar.gz",
            "host_source": "host-source-before.tar.gz",
        },
        "git_commit_or_push_performed": False,
    }
)
(P / "post-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(
    json.dumps(
        {
            "status": report["status"],
            "services": len(S),
            "workers": len(workers),
            "public_urls_verified": len(report["public_http"]),
            "avatar_templates": len(M["avatar_changes"]),
        }
    )
)
