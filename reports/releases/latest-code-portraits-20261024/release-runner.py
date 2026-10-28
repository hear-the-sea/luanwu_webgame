"""定向数据同步的完整发布：prepare 仅构建预检；deploy 在停写后同步。"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import deploy_lib as lib

P, ROOT, M, SERVICES = lib.P, lib.ROOT, lib.M, lib.SERVICES
BOX_KEYS = (
    "guild_skill_book_box_blue",
    "guild_skill_book_box_purple",
    "guild_skill_book_box_refined",
    "guild_skill_book_box_master",
    "guild_gear_box_refined_purple",
    "guild_gear_box_master",
)
ITEM_KEYS = (
    "mingjiao_holy_fire_token",
    "mingjiao_fire_seed",
    "xiaozhao_recruit_scroll",
    *BOX_KEYS,
)


def hashes(container):
    code = (
        "import hashlib,json;from pathlib import Path;names="
        + repr(list(M["files"]))
        + ';print(json.dumps({n:hashlib.sha256((Path("/app")/n).read_bytes()).hexdigest() for n in names}))'
    )
    return json.loads(lib.run(["docker", "exec", "-i", container, "python", "-"], input=code))


def verify_previous_sources():
    names = list(M["previous_files"])
    code = (
        "import hashlib,json;from pathlib import Path;names="
        + repr(names)
        + ';print(json.dumps({n:hashlib.sha256((Path("/app")/n).read_bytes()).hexdigest() for n in names}))'
    )
    actual = json.loads(lib.run(["docker", "exec", "-i", "code-web-1", "python", "-"], input=code))
    assert actual == M["previous_files"], "Production source changed after preflight snapshot"


def management(*args):
    return lib.run(
        lib.compose()
        + [
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--entrypoint",
            "python",
            "web",
            "manage.py",
            *args,
        ],
        timeout=900,
    )


def reload_proxy(text):
    config = Path(json.loads((P / "before.json").read_text())["caddy_path"])
    with config.open("w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    lib.run(
        [
            "docker",
            "exec",
            "code-caddy-1",
            "caddy",
            "reload",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ]
    )


def targeted_snapshot():
    return lib.app(
        """import hashlib
from django.core.serializers.json import DjangoJSONEncoder
from guests.models import Guest,GuestSkill,WorldUniqueGuest
from gameplay.models import InventoryItem,Manor,JailPrisoner
values={
 'guests':list(Guest.objects.order_by('id').values()),
 'inventory':list(InventoryItem.objects.order_by('id').values()),
 'prisoners':list(JailPrisoner.objects.order_by('id').values()),
 'unique_states':list(WorldUniqueGuest.objects.order_by('id').values('id','template_id','status','owner_guest_id','owner_manor_id','version'))}
print(json.dumps({'guest_ids':[g['id'] for g in values['guests']],
 'guest_skills':list(GuestSkill.objects.order_by('id').values_list('id','guest_id','skill_id')),
 'inventory_count':len(values['inventory']),'manor_count':Manor.objects.count(),
 'player_digest':hashlib.sha256(json.dumps(values,sort_keys=True,cls=DjangoJSONEncoder).encode()).hexdigest()}))
"""
    )


def prepare():
    lib.phase("verifying_full_release")
    assert hashlib.sha256((P / "release.tar.gz").read_bytes()).hexdigest() == M["archive_sha256"]
    with tarfile.open(P / "release.tar.gz") as archive:
        assert sorted(archive.getnames()) == sorted(M["files"])
        archive.extractall(P / "app", filter="data")
    for name, digest in M["files"].items():
        assert hashlib.sha256((P / "app" / name).read_bytes()).hexdigest() == digest, name
    web = lib.inspect("code-web-1")
    assert web["Config"]["Image"] == M["expected_base_image"]
    verify_previous_sources()
    identities = {}
    for service in SERVICES:
        info = lib.inspect("code-" + service + "-1")
        assert info["State"]["Running"] and info["Config"]["Image"] == M["expected_base_image"]
        identities[service] = {
            "image": info["Config"]["Image"],
            "memory": info["HostConfig"]["Memory"],
            "reservation": info["HostConfig"]["MemoryReservation"],
            "swap": info["HostConfig"]["MemorySwap"],
        }
    labels = web["Config"]["Labels"]
    command = [
        "docker",
        "compose",
        "--project-name",
        labels["com.docker.compose.project"],
        "--project-directory",
        labels["com.docker.compose.project.working_dir"],
    ]
    for name in labels["com.docker.compose.project.config_files"].split(","):
        assert Path(name).is_file()
        command += ["-f", name]
    for name in labels.get("com.docker.compose.project.environment_file", "").split(","):
        if name:
            command += ["--env-file", name]
    lock = lib.run(["docker", "exec", "code-web-1", "cat", "/app/requirements.lock.txt"])
    assert hashlib.sha256(lock.encode()).hexdigest() == M["dependency_sha256"]
    caddy = lib.inspect("code-caddy-1")
    caddy_path = Path(next(x["Source"] for x in caddy["Mounts"] if x["Destination"] == "/etc/caddy/Caddyfile"))
    original = caddy_path.read_text()
    assert len(re.findall(r"(?m)^\s*reverse_proxy\s+web:8000\s*$", original)) == 1
    (P / "Caddyfile.before").write_text(original)
    (P / "before.json").write_text(
        json.dumps(
            {
                "compose": command,
                "caddy_path": str(caddy_path),
                "services": identities,
                "image": M["expected_base_image"],
            },
            indent=2,
        )
    )
    override = {
        "services": {
            s: {
                "image": M["image"],
                "environment": {
                    "DJANGO_RUN_MIGRATIONS": "0",
                    "DJANGO_SYNC_GUEST_TEMPLATES": "0",
                    "DJANGO_COLLECTSTATIC": "0",
                },
            }
            for s in SERVICES
        }
    }
    (P / "compose.release.json").write_text(json.dumps(override, indent=2))
    command += ["-f", str(P / "compose.release.json")]
    (P / "compose-command.json").write_text(json.dumps(command))
    (P / "Dockerfile").write_text(
        "FROM "
        + M["expected_base_image"]
        + '\nCOPY --chown=10001:10001 app/ /app/\nLABEL webgame.release="'
        + M["release"]
        + '"\n'
    )
    with tarfile.open(P / "host-source-before.tar.gz", "w:gz") as archive:
        for name in M["files"]:
            source = ROOT / name
            if source.is_file():
                assert not source.is_symlink()
                archive.add(source, arcname=name, recursive=False)
    lib.phase("building_and_checking_release")
    lib.run(["docker", "build", "-t", M["image"], str(P)], timeout=900)
    lib.run(command + ["config", "--quiet"])
    model = json.loads(lib.run(command + ["config", "--format", "json"]))
    for service in SERVICES:
        assert int(model["services"][service]["mem_limit"]) == identities[service]["memory"]
    for args in (
        ["check"],
        ["validate_yaml_configs", "--dry-run"],
        ["makemigrations", "--check", "--dry-run", "--skip-checks"],
    ):
        management(*args)
    assert lib.pending_migrations() == M["expected_pending_migrations"]
    (P / "battle-image-smoke.json").write_text(json.dumps(lib.battle_smoke(), indent=2))
    lib.phase("backing_up_database_and_media")
    dump = [
        "docker",
        "exec",
        "code-db-1",
        "sh",
        "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --routines --triggers --events --set-gtid-purged=OFF --no-tablespaces --databases webgame',
    ]
    with (P / "backup-errors.log").open("wb") as errors, (P / "database-before.sql.gz").open("wb") as output:
        source = subprocess.Popen(dump, stdout=subprocess.PIPE, stderr=errors)
        compressor = subprocess.Popen(["gzip", "-1"], stdin=source.stdout, stdout=output, stderr=errors)
        source.stdout.close()
        assert compressor.wait(timeout=1800) == 0 and source.wait(timeout=60) == 0
    assert (P / "database-before.sql.gz").stat().st_size > 100000
    lib.run(["gzip", "-t", str(P / "database-before.sql.gz")], timeout=900)
    lib.run(
        [
            "tar",
            "-czf",
            str(P / "media-static-before.tar.gz"),
            "-C",
            str(ROOT / "runtime"),
            "media",
            "staticfiles",
        ],
        timeout=600,
    )
    (P / "prepared.json").write_text(json.dumps({"image": M["image"]}, indent=2))
    lib.phase("prepared")


def backup_at_cutover():
    dump = [
        "docker",
        "exec",
        "code-db-1",
        "sh",
        "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --set-gtid-purged=OFF --no-tablespaces webgame guests_guesttemplate django_migrations',
    ]
    path = P / "database-at-cutover.sql.gz"
    with (P / "cutover-backup-errors.log").open("wb") as errors, path.open("wb") as output:
        source = subprocess.Popen(dump, stdout=subprocess.PIPE, stderr=errors)
        compressor = subprocess.Popen(["gzip", "-1"], stdin=source.stdout, stdout=output, stderr=errors)
        source.stdout.close()
        assert compressor.wait(timeout=1800) == 0 and source.wait(timeout=60) == 0
    assert path.stat().st_size > 1000
    lib.run(["gzip", "-t", str(path)], timeout=900)


def sync_templates():
    code = (P / "sync-templates.py").read_text()
    return lib.app(code)


def template_state():
    return lib.app(
        """from guests.models import GuestTemplate
print(json.dumps(list(GuestTemplate.objects.order_by('id').values())))
"""
    )


def verify_templates(before, after):
    expected = {r["key"]: r["target"] for r in M["avatar_changes"]}
    growth_keys = {"hero_yang_zheng_purple", "hero_di_qinglin"}
    assert len(before) == len(after)
    for a, b in zip(before, after):
        wanted = dict(a)
        if a["key"] in expected:
            wanted["avatar"] = expected[a["key"]]
        if a["key"] in growth_keys:
            wanted["growth_range"] = [6, 13]
        assert wanted == b, ("Unexpected template change", a["key"])


def smoke(live=False):
    return lib.app((P / "runtime-smoke.py").read_text(), live=live)


def deploy():
    assert json.loads((P / "prepared.json").read_text())["image"] == M["image"]
    assert json.loads((P / "offline-verification.json").read_text())["status"] == "passed"
    before = json.loads((P / "before.json").read_text())
    original = (P / "Caddyfile.before").read_text()
    assert lib.inspect("code-web-1")["Config"]["Image"] == M["expected_base_image"]
    assert Path(before["caddy_path"]).read_text() == original
    verify_previous_sources()
    assert lib.pending_migrations() == []
    maintenance = re.sub(
        r"(?m)^(\s*)reverse_proxy\s+web:8000\s*$",
        r'\1respond "Game update in progress. Please refresh shortly." 503',
        original,
        count=1,
    )
    reload_proxy(maintenance)
    lib.phase("maintenance_draining_requests", maintenance=True)
    for _ in range(5):
        time.sleep(13)
    lib.phase("stopping_application_writers", maintenance=True)
    lib.run(lib.compose() + ["stop", "--timeout", "180", "beat"], timeout=240)
    lib.run(lib.compose() + ["stop", "--timeout", "180", *SERVICES[:-1]], timeout=420)
    backup_at_cutover()
    snapshot = targeted_snapshot()
    templates = template_state()
    (P / "player-state-before.json").write_text(json.dumps(snapshot, indent=2))
    (P / "templates-before.json").write_text(json.dumps(templates, indent=2))
    lib.phase("synchronizing_growth_and_portraits", maintenance=True)
    updated = sync_templates()
    verify_templates(templates, template_state())
    assert snapshot == targeted_snapshot(), "Player data changed during template sync"
    (P / "targeted-sync.json").write_text(json.dumps(updated, indent=2))
    management("collectstatic", "--noinput")
    lib.phase("replacing_application_services", maintenance=True)
    lib.run(
        lib.compose() + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", *SERVICES],
        timeout=420,
    )
    lib.phase("waiting_for_readiness", maintenance=True)
    deadline = time.monotonic() + 300
    while True:
        try:
            ready = subprocess.run(
                ["docker", "exec", "code-web-1", "python", "scripts/check_web_readiness.py"],
                capture_output=True,
                timeout=25,
            )
        except subprocess.TimeoutExpired:
            ready = None
        if ready is not None and ready.returncode == 0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Readiness failed; maintenance retained")
        time.sleep(3)
    lib.phase("verifying_every_application_service", maintenance=True)
    for service in SERVICES:
        info = lib.inspect("code-" + service + "-1")
        assert info["State"]["Running"] and info["Config"]["Image"] == M["image"]
        assert info["HostConfig"]["Memory"] == before["services"][service]["memory"]
        assert hashes("code-" + service + "-1") == M["files"], service + " source mismatch"
    summary = smoke(live=True)
    assert lib.pending_migrations() == []
    for name, digest in M["files"].items():
        destination = ROOT / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(P / "app" / name, destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    summary.update(
        {
            "image": M["image"],
            "services": len(SERVICES),
            "runtime_files_verified_per_service": len(M["files"]),
            "pending_migrations": 0,
            "player_records_preserved": True,
            "template_other_fields_preserved": True,
            "memory_limits_preserved": True,
            "avatar_templates_updated": updated,
        }
    )
    (P / "verification.json").write_text(json.dumps(summary, indent=2))
    reload_proxy(original)
    lib.phase("complete", maintenance=False)


if __name__ == "__main__":
    with (ROOT / ".web-blue-green.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            {"prepare": prepare, "deploy": deploy}[sys.argv[1]]()
        except Exception as error:
            lib.phase("failed_" + type(error).__name__, message=str(error))
            raise
