"""受控赛季发布：不加载其他玩法模板，不清理队列，不删除旧静态资源。"""

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
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)
ROOT = Path("/root/code")
P = Path(__file__).resolve().parent
M = json.loads((P / "release-manifest.json").read_text())
SERVICES = [
    "web",
    "worker",
    "worker_battle",
    "worker_timer",
    "worker_timer_scan",
    "worker_timer_safety",
    "worker_timer_maintenance",
    "beat",
]


def phase(name, **details):
    record = {"phase": name, "image": M["image"], "updated_at": datetime.now(timezone.utc).isoformat(), **details}
    (P / "phase.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps(record, ensure_ascii=False), flush=True)


def run(args, *, text=None, timeout=900):
    result = subprocess.run(args, input=text, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    with (P / "commands.log").open("a") as log:
        log.write(result.stdout + result.stderr + "\n")
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: exit {result.returncode}; inspect private commands.log")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def compose():
    return json.loads((P / "compose-command.json").read_text())


def app(code, *, live=False):
    setup = 'import os,json;os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings");import django;django.setup()\n'
    command = (
        ["docker", "exec", "-i", "code-web-1", "python", "-"]
        if live
        else compose() + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "web", "-"]
    )
    return json.loads(run(command, text=setup + code))


def migration_plan():
    return app(
        "from django.db import connection\nfrom django.db.migrations.executor import MigrationExecutor\n"
        "e=MigrationExecutor(connection)\nprint(json.dumps([[m.app_label,m.name,backward] "
        "for m,backward in e.migration_plan(e.loader.graph.leaf_nodes())]))\n"
    )


def asset_snapshot():
    return app(
        """from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
import hashlib
labels=['gameplay.Manor','gameplay.InventoryItem','gameplay.PlayerTroop','gameplay.TroopBankStorage',
        'guests.Guest','guests.GuestSkill','guests.GearItem']
result={}
for label in labels:
 model=apps.get_model(label); digest=hashlib.sha256(); count=0
 for row in model.objects.order_by('pk').values().iterator(chunk_size=500):
  digest.update(json.dumps(row,cls=DjangoJSONEncoder,sort_keys=True,separators=(',',':')).encode())
  count+=1
 result[label]={'rows':count,'sha256':digest.hexdigest()}
print(json.dumps(result))
"""
    )


def prepare():
    phase("validating_live_baseline")
    web = inspect("code-web-1")
    assert web["Config"]["Image"] == M["base_image"], "Live image changed"
    identities = {}
    for service in SERVICES:
        info = inspect("code-" + service + "-1")
        assert info["State"]["Running"] and info["Config"]["Image"] == M["base_image"], service
        identities[service] = {key: info["HostConfig"][key] for key in ("Memory", "MemoryReservation", "MemorySwap")}
    labels = web["Config"]["Labels"]
    cmd = [
        "docker",
        "compose",
        "--project-name",
        labels["com.docker.compose.project"],
        "--project-directory",
        labels["com.docker.compose.project.working_dir"],
    ]
    for filename in labels["com.docker.compose.project.config_files"].split(","):
        assert Path(filename).is_file(), "Missing live compose file"
        cmd += ["-f", filename]
    for filename in labels.get("com.docker.compose.project.environment_file", "").split(","):
        if filename:
            cmd += ["--env-file", filename]
    caddy = inspect("code-caddy-1")
    config = Path(next(m["Source"] for m in caddy["Mounts"] if m["Destination"] == "/etc/caddy/Caddyfile"))
    original = config.read_text()
    assert len(re.findall(r"(?m)^\s*reverse_proxy\s+web:8000\s*$", original)) == 1
    (P / "Caddyfile.before").write_text(original)
    static = Path(next(m["Source"] for m in web["Mounts"] if m["Destination"] == "/app/staticfiles"))
    before = {"compose": cmd, "caddy_path": str(config), "static_dir": str(static), "services": identities}
    (P / "before.json").write_text(json.dumps(before, indent=2))
    override = {
        "services": {
            name: {
                "image": M["image"],
                "pull_policy": "never",
                "environment": {
                    "DJANGO_RUN_MIGRATIONS": "0",
                    "DJANGO_SYNC_GUEST_TEMPLATES": "0",
                    "DJANGO_COLLECTSTATIC": "0",
                },
            }
            for name in SERVICES
        }
    }
    (P / "compose.release.json").write_text(json.dumps(override, indent=2))
    (P / "compose-command.json").write_text(json.dumps(cmd + ["-f", str(P / "compose.release.json")]))
    archive = P / "season-candidate.tar.gz"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == M["archive_sha256"]
    with tarfile.open(archive) as content:
        assert sorted(content.getnames()) == sorted(M["files"])
        content.extractall(P / "payload", filter="data")
    for filename, expected in M["files"].items():
        assert hashlib.sha256((P / "payload" / filename).read_bytes()).hexdigest() == expected, filename
    phase("building_season_overlay_image")
    run(["docker", "build", "-f", str(P / "Dockerfile.release"), "-t", M["image"], str(P)])
    run(compose() + ["config", "--quiet"])
    run(compose() + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "web", "manage.py", "check"])
    expected = [
        ["gameplay", Path(name).stem, False] for name in sorted(M["files"]) if name.startswith("gameplay/migrations/")
    ]
    plan = migration_plan()
    assert plan == expected, "Unexpected migration plan"
    (P / "migration-plan.json").write_text(json.dumps(plan, indent=2))
    catalog = app(
        """from gameplay.services.season.catalog import load_rules,reward_catalog,shop_catalog,faction_template_keys
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.models import ItemTemplate
from guests.models import GuestTemplate
rules=load_rules(); campaign=load_campaign_rules()
keys=set()
for side in ('fiction','history'):
 for row in [*reward_catalog(side,rules=rules),*shop_catalog(side,rules=rules)]:keys.update(row.get('items',{}))
keys.update(rules['summon_fallback_items'])
keys.update(campaign['rewards']['winner_items'])
for row in campaign['rewards']['tiers']:keys.update(row['items'])
missing=sorted(keys-set(ItemTemplate.objects.filter(key__in=keys).values_list('key',flat=True)))
counts={side:GuestTemplate.objects.filter(key__in=faction_template_keys(side,rules=rules),recruitable=True).count() for side in ('fiction','history')}
assert not missing, 'Missing season item templates: '+repr(missing)
assert all(counts.values()),'Empty summon catalog'
print(json.dumps({'item_templates':len(keys),'missing':missing,'recruitable_templates':counts}))
"""
    )
    (P / "catalog-preflight.json").write_text(json.dumps(catalog, indent=2))
    phase("backing_up_live_database")
    backup_source = Path(M["database_backup_source"])
    assert backup_source.is_file() and backup_source.stat().st_size > 0
    run(["gzip", "-t", str(backup_source)])
    os.link(backup_source, P / "database-before.sql.gz")
    phase("backing_up_static_media_and_host_sources")
    run(["tar", "-czf", str(P / "media-static-before.tar.gz"), "-C", str(ROOT / "runtime"), "media", "staticfiles"])
    with tarfile.open(P / "host-source-before.tar.gz", "w:gz") as backup:
        for name in M["files"]:
            source = ROOT / name
            if source.is_file():
                assert not source.is_symlink(), "Unexpected source symlink"
                backup.add(source, arcname=name, recursive=False)
    for name in ("staticfiles.json",):
        if (static / name).is_file():
            shutil.copy2(static / name, P / (name + ".before"))
    phase("preparing_hashed_static_assets")
    run(
        compose()
        + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "web", "manage.py", "collectstatic", "--noinput"]
    )
    # 显式 collectstatic 的发布也必须生成 WebP；服务启动时已跳过该步骤。
    run(
        compose()
        + [
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--entrypoint",
            "python",
            "web",
            "-m",
            "scripts.prepare_webp_images",
            "--root",
            "staticfiles/season-assets",
            "--root",
            "staticfiles/season-troops",
            "--apply",
        ]
    )
    static_check = app(
        """from django.contrib.staticfiles.storage import staticfiles_storage
from pathlib import Path
assets=['css/style.css','css/season.css','css/season-visual.css','css/map-wooden-sign.css',
        'js/season.js','images/season-icons.svg','season-assets/mountain/background.png',
        'season-assets/river/background.png','season-assets/forest/background.png','season-troops/daosheng.png']
print(json.dumps({key:staticfiles_storage.url(key) for key in assets}))
"""
    )
    (P / "static-check.json").write_text(json.dumps(static_check, indent=2))
    phase(
        "prepared",
        database_backup_bytes=(P / "database-before.sql.gz").stat().st_size,
        item_templates=catalog["item_templates"],
        pending_migrations=len(plan),
    )


def reload_proxy(config, text):
    with config.open("w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    run(
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


def deploy():
    assert json.loads((P / "phase.json").read_text())["phase"] == "prepared"
    assert inspect("code-web-1")["Config"]["Image"] == M["base_image"], "Live base changed"
    before = json.loads((P / "before.json").read_text())
    config = Path(before["caddy_path"])
    original = (P / "Caddyfile.before").read_text()
    assert config.read_text() == original, "Proxy changed since preflight"
    maintenance = re.sub(
        r"(?m)^(\s*)reverse_proxy\s+web:8000\s*$",
        r'\1respond "Game update in progress. Please refresh shortly." 503',
        original,
        count=1,
    )
    reload_proxy(config, maintenance)
    try:
        phase("maintenance_draining_requests", maintenance=True)
        time.sleep(65)
        phase("stopping_application_writers", maintenance=True)
        run(compose() + ["stop", "--timeout", "180", "beat"], timeout=240)
        run(compose() + ["stop", "--timeout", "180", *SERVICES[:-1]], timeout=420)
        snapshot = asset_snapshot()
        (P / "player-assets-before.json").write_text(json.dumps(snapshot, indent=2))
        phase("applying_season_migrations", maintenance=True)
        run(
            compose()
            + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "web", "manage.py", "migrate", "--noinput"],
            timeout=900,
        )
        assert migration_plan() == []
        phase("starting_first_season_and_readonly_combat_smoke", maintenance=True)
        season = app(
            """from gameplay.models import Season
from django.core.management import call_command
from io import StringIO
if not Season.objects.exists():call_command('start_season',stdout=StringIO())
s=Season.objects.filter(closed=False).order_by('id').first()
assert s is not None
print(json.dumps({'id':s.pk,'name':s.name,'starts_at':s.starts_at.isoformat(),'ends_at':s.ends_at.isoformat(),'member_count':s.members.count()}))
"""
        )
        (P / "season-start.json").write_text(json.dumps(season, ensure_ascii=False, indent=2))
        smoke = app((P / "battle-smoke.py").read_text())
        (P / "battle-smoke.json").write_text(json.dumps(smoke, indent=2))
        after = asset_snapshot()
        (P / "player-assets-after.json").write_text(json.dumps(after, indent=2))
        assert after == snapshot, "Existing player assets changed during migration"
        phase("starting_web_and_workers", maintenance=True, player_assets_preserved=True)
        run(compose() + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", *SERVICES[:-1]])
        deadline = time.monotonic() + 120
        while True:
            result = subprocess.run(
                ["docker", "exec", "code-web-1", "python", "scripts/check_web_readiness.py"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode == 0:
                break
            assert time.monotonic() < deadline, "New Web readiness failed"
            time.sleep(2)
        for service in SERVICES[:-1]:
            info = inspect("code-" + service + "-1")
            assert info["State"]["Running"] and info["Config"]["Image"] == M["image"], service
            assert all(info["HostConfig"][key] == value for key, value in before["services"][service].items()), service
        phase("starting_single_beat", maintenance=True)
        run(compose() + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "beat"])
        identities = {}
        for service in SERVICES:
            info = inspect("code-" + service + "-1")
            assert info["State"]["Running"] and info["Config"]["Image"] == M["image"], service
            assert all(info["HostConfig"][key] == value for key, value in before["services"][service].items()), service
            identities[service] = {"image": info["Config"]["Image"], "running": info["State"]["Running"]}
        # 所有应用服务已就绪后，才同步主机上的同批源文件。
        for name in M["files"]:
            target = ROOT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(P / "payload" / name, target)
        (P / "services-after.json").write_text(json.dumps(identities, indent=2))
        reload_proxy(config, original)
        phase("deployed_pending_public_verification", maintenance=False, player_assets_preserved=True, season=season)
    except BaseException:
        phase("rolling_back_application_image", maintenance=True)
        rollback = {
            "services": {
                name: {
                    "image": M["base_image"],
                    "pull_policy": "never",
                    "environment": {
                        "DJANGO_RUN_MIGRATIONS": "0",
                        "DJANGO_SYNC_GUEST_TEMPLATES": "0",
                        "DJANGO_COLLECTSTATIC": "0",
                    },
                }
                for name in SERVICES
            }
        }
        (P / "compose.rollback.json").write_text(json.dumps(rollback, indent=2))
        old = before["compose"] + ["-f", str(P / "compose.rollback.json")]
        run(old + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", *SERVICES])
        run(["docker", "exec", "code-web-1", "python", "scripts/check_web_readiness.py"])
        if (P / "staticfiles.json.before").is_file():
            shutil.copy2(P / "staticfiles.json.before", Path(before["static_dir"]) / "staticfiles.json")
        reload_proxy(config, original)
        phase("rolled_back_to_previous_image", maintenance=False)
        raise


if __name__ == "__main__":
    with (ROOT / ".web-blue-green.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            {"prepare": prepare, "deploy": deploy}[sys.argv[1]]()
        except BaseException as error:
            phase("failed", error_type=type(error).__name__, message=str(error))
            raise
