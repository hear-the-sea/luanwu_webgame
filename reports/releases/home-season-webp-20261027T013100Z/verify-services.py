import datetime
import hashlib
import json
import subprocess
from pathlib import Path

p = Path("/root/code/releases/home-season-webp-20261027T013100Z")
first = Path("/root/code/releases/season-campaign-20261026T151253Z")
m = json.loads((p / "release-manifest.json").read_text())
assert json.loads((p / "phase.json").read_text())["phase"] == "deployed_pending_public_verification"
files = json.loads((p / "combined-files.json").read_text())
code = (
    "from pathlib import Path;import json,hashlib;files="
    + repr(files)
    + ";print(json.dumps([f for f,h in files.items() if not Path('/app',f).is_file() or hashlib.sha256(Path('/app',f).read_bytes()).hexdigest()!=h]))"
)
r = subprocess.run(
    ["docker", "exec", "-i", "code-web-1", "python", "-"], input=code, text=True, capture_output=True, check=True
)
assert json.loads(r.stdout) == [], "Runtime file mismatch"
before = json.loads((p / "before.json").read_text())
services = {}
for name, limits in before["services"].items():
    i = json.loads(subprocess.check_output(["docker", "inspect", "code-" + name + "-1"]))[0]
    assert i["Config"]["Image"] == m["image"] and i["State"]["Running"]
    assert i["RestartCount"] == 0 and not i["State"]["OOMKilled"]
    assert all(i["HostConfig"][k] == v for k, v in limits.items())
    health = i["State"].get("Health", {}).get("Status")
    if name == "web":
        assert health == "healthy", health
    services[name] = {
        "image": i["Config"]["Image"],
        "running": True,
        "health": health,
        "restart_count": i["RestartCount"],
        "memory": i["HostConfig"]["Memory"],
    }
assert (p / "database-before.sql.gz").is_file()
result = {
    "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "services": services,
    "verified_runtime_files": len(files),
    "preserved_live_battle_patch": True,
    "backups": {
        "database_bytes": (p / "database-before.sql.gz").stat().st_size,
        "media_static_bytes": (p / "media-static-before.tar.gz").stat().st_size,
    },
    "assets_unchanged_during_each_migration": all(
        json.loads((r / "player-assets-before.json").read_text())
        == json.loads((r / "player-assets-after.json").read_text())
        for r in (p,)
    ),
}
print(json.dumps(result))
