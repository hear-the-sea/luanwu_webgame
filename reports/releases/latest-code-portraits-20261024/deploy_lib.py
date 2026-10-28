import fcntl
import gzip
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


def phase(name, **fields):
    data = {"phase": name, "image": M["image"], "updated_at_utc": datetime.now(timezone.utc).isoformat(), **fields}
    (P / "phase.json").write_text(json.dumps(data, indent=2))
    print(json.dumps(data), flush=True)


def run(argv, *, input=None, timeout=600):
    result = subprocess.run(argv, input=input, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    with (P / "commands.log").open("a") as log:
        log.write(result.stdout + result.stderr + "\n")
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed with exit {result.returncode}; inspect commands.log")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def compose():
    return json.loads((P / "compose-command.json").read_text())


def app(code, *, live=False):
    command = (
        ["docker", "exec", "-i", "code-web-1", "python", "-"]
        if live
        else compose() + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "web", "-"]
    )
    setup = "import os,json; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); import django; django.setup()\n"
    return json.loads(run(command, input=setup + code))


def pending_migrations():
    return app(
        """from django.db import connection
from django.db.migrations.executor import MigrationExecutor
e=MigrationExecutor(connection)
print(json.dumps([[m.app_label,m.name,backwards] for m,backwards in e.migration_plan(e.loader.graph.leaf_nodes())]))
"""
    )


def template_preflight():
    return app(
        """from io import StringIO
from pathlib import Path
from django.conf import settings
from guests.management.commands.load_guest_templates import Command
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.models import Guest,GuestTemplate,GuestSkill
c=Command(stdout=StringIO())
s=c._build_sections(c._load_main_payload(Path(settings.BASE_DIR)/'data/guest_templates.yaml'),c._load_skills_payload(''),c._load_heroes_payload(''))
c._append_hero_roster_templates(s['template_data'],s['hero_roster'],s['attribute_profiles'],0)
keys={row['key'] for row in s['template_data']}
missing=Guest.objects.exclude(template__key__in=keys).exclude(template__key__startswith=RARITY_CONVERSION_TEMPLATE_KEY_PREFIX)
assert not missing.exists(),'Config reload would remove owned guest templates'
skills=c._collect_desired_skill_keys(s['skill_data'],s['book_data'],{row['key']:row.get('skills',[]) for row in s['template_data']})
assert not GuestSkill.objects.exclude(skill__key__in=skills).exists(),'Config reload would remove owned guest skills'
print(json.dumps({'desired_guest_templates':len(keys),'owned_guests_checked':Guest.objects.count(),'missing_owned_template_count':0,'missing_owned_skill_count':0}))
"""
    )


def battle_smoke(*, live=False):
    return app(
        """import random
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION
from battle.combatants_pkg.core import Combatant
from battle.simulation.attack_execution import perform_attack
from battle.simulation.constants import TROOP_SCREENING_CHANCE,GUEST_SKILL_VS_TROOP_MULTIPLIER
from guests.models import Guest,GuestTemplate
from battle.report_events import troop_phase_skill
assert CURRENT_BATTLE_ENGINE_VERSION=='8'
assert TROOP_SCREENING_CHANCE==0.75 and GUEST_SKILL_VS_TROOP_MULTIPLIER==3
class Rng(random.Random):
    def random(self):return 0.99
    def choice(self,sequence):return sequence[0]
    def shuffle(self,sequence):pass
    def uniform(self,a,b):return 1.0
def unit(name,side):
    return Combatant(name=name,kind='guest',side=side,attack=100,defense=0,hp=10000,max_hp=10000,initial_hp=10000,luck=100,agility=100,priority=0,rarity='purple',troop_strength=0,skills=[])
a=unit('攻击者','attacker');b=unit('防守者','defender')
a.skills=[{'key':'smoke','name':'测试爆发','kind':'active','trigger_chance':1.0,'damage_formula':{'base':100},'targets':1}]
event=perform_attack(a,[a],[b],Rng())
assert event['attack_phase']=='burst' and event['additional_attacks'][0]['attack_phase']=='normal'
g=Guest(template=GuestTemplate(base_hp=1000),level=100,defense_stat=100)
assert g.max_hp==11940
assert troop_phase_skill({'kind':'troop','actor_template_key':'arrow_god','priority':-1,'priority_phase':-1})=='凤舞九天'
print(json.dumps({'engine':CURRENT_BATTLE_ENGINE_VERSION,'burst_then_normal':True,'troop_screening_chance':TROOP_SCREENING_CHANCE,'skill_increment_vs_troop':GUEST_SKILL_VS_TROOP_MULTIPLIER,'level_hp_preserved':True,'bow_phase_name':True}))
""",
        live=live,
    )


def prepare():
    phase("verifying_full_release")
    assert hashlib.sha256((P / "release.tar.gz").read_bytes()).hexdigest() == M["archive_sha256"]
    with tarfile.open(P / "release.tar.gz") as archive:
        assert sorted(archive.getnames()) == sorted(M["files"])
        archive.extractall(P / "app", filter="data")
    for name, digest in M["files"].items():
        assert hashlib.sha256((P / "app" / name).read_bytes()).hexdigest() == digest, name
    web = inspect("code-web-1")
    assert web["Config"]["Image"] == M["expected_base_image"], "Production image changed"
    identities = {}
    for service in SERVICES:
        i = inspect("code-" + service + "-1")
        assert i["State"]["Running"] and i["Config"]["Image"] == M["expected_base_image"]
        identities[service] = {
            "image": i["Config"]["Image"],
            "memory": i["HostConfig"]["Memory"],
            "reservation": i["HostConfig"]["MemoryReservation"],
            "swap": i["HostConfig"]["MemorySwap"],
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
    for filename in labels["com.docker.compose.project.config_files"].split(","):
        assert Path(filename).is_file()
        command += ["-f", filename]
    for filename in labels.get("com.docker.compose.project.environment_file", "").split(","):
        if filename:
            command += ["--env-file", filename]
    lock = run(["docker", "exec", "code-web-1", "cat", "/app/requirements.lock.txt"])
    assert hashlib.sha256(lock.encode()).hexdigest() == M["dependency_sha256"], "Dependencies changed"
    caddy = inspect("code-caddy-1")
    caddy_path = Path(next(m["Source"] for m in caddy["Mounts"] if m["Destination"] == "/etc/caddy/Caddyfile"))
    original = caddy_path.read_text()
    assert len(re.findall(r"(?m)^\s*reverse_proxy\s+web:8000\s*$", original)) == 1
    (P / "Caddyfile.before").write_text(original)
    (P / "before.json").write_text(
        json.dumps(
            {
                "image": M["expected_base_image"],
                "compose": command,
                "caddy_path": str(caddy_path),
                "services": identities,
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
    phase("building_complete_runtime_image")
    run(["docker", "build", "-t", M["image"], str(P)], timeout=900)
    run(command + ["config", "--quiet"])
    model = json.loads(run(command + ["config", "--format", "json"]))
    for s in ("worker_battle", "worker_timer_scan"):
        assert int(model["services"][s]["mem_limit"]) == 384 * 1024 * 1024, "Memory expansion lost"
    for args in (
        ["check"],
        ["validate_yaml_configs", "--dry-run"],
        ["makemigrations", "--check", "--dry-run", "--skip-checks"],
    ):
        run(
            command + ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "web", "manage.py", *args],
            timeout=180,
        )
    assert pending_migrations() == M["expected_pending_migrations"], "Unexpected migrations"
    (P / "config-preflight.json").write_text(json.dumps(template_preflight(), indent=2))
    (P / "battle-image-smoke.json").write_text(json.dumps(battle_smoke(), indent=2))
    phase("backing_up_database")
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
        assert compressor.wait(timeout=1800) == 0 and source.wait(timeout=60) == 0, "Database backup failed"
    assert (P / "database-before.sql.gz").stat().st_size > 100000
    run(["gzip", "-t", str(P / "database-before.sql.gz")], timeout=900)
    phase("backing_up_media_and_static")
    run(
        ["tar", "-czf", str(P / "media-static-before.tar.gz"), "-C", str(ROOT / "runtime"), "media", "staticfiles"],
        timeout=600,
    )
    (P / "prepared.json").write_text(
        json.dumps(
            {"image": M["image"], "database_backup_bytes": (P / "database-before.sql.gz").stat().st_size}, indent=2
        )
    )
    phase("prepared")


def deploy():
    assert json.loads((P / "prepared.json").read_text())["image"] == M["image"]
    assert (P / "offline-verification.json").is_file(), "Offline validation not recorded"
    before = json.loads((P / "before.json").read_text())
    original = (P / "Caddyfile.before").read_text()
    config = Path(before["caddy_path"])
    assert inspect("code-web-1")["Config"]["Image"] == before["image"] and config.read_text() == original
    assert pending_migrations() == M["expected_pending_migrations"]
    template_preflight()

    def reload_proxy(text):
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

    maintenance = re.sub(
        r"(?m)^(\s*)reverse_proxy\s+web:8000\s*$",
        r'\1respond "Game update in progress. Please refresh shortly." 503',
        original,
        count=1,
    )
    reload_proxy(maintenance)
    phase("maintenance_draining_requests", maintenance=True)
    time.sleep(65)
    phase("stopping_application_writers", maintenance=True)
    run(compose() + ["stop", "--timeout", "180", "beat"], timeout=240)
    run(compose() + ["stop", "--timeout", "180", *SERVICES[:-1]], timeout=420)
    snapshot = app(
        """from guests.models import Guest,GuestTemplate,GuestSkill
from gameplay.models import BotRuntimeRoutingState,InventoryItem,Manor,PlayerTroop
from django.core.serializers.json import DjangoJSONEncoder
print(json.dumps({'guest_ids':list(Guest.objects.order_by('id').values_list('id',flat=True)),
 'guest_skills':list(GuestSkill.objects.order_by('id').values('id','guest_id','skill_id')),
 'routing':list(BotRuntimeRoutingState.objects.order_by('key').values()),
 'manor_count':Manor.objects.count(),'guest_count':Guest.objects.count(),'inventory_count':InventoryItem.objects.count(),'troop_rows':PlayerTroop.objects.count()},cls=DjangoJSONEncoder))
"""
    )
    (P / "player-records-before.json").write_text(json.dumps(snapshot, indent=2))
    phase("backing_up_quiescent_market_tables", maintenance=True)
    market_dump = run(
        [
            "docker",
            "exec",
            "code-db-1",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --quick --set-gtid-purged=OFF --no-tablespaces webgame trade_market_listing trade_market_transaction',
        ],
        timeout=180,
    )
    with gzip.open(P / "market-tables-before.sql.gz", "wt") as backup:
        backup.write(market_dump)
    phase("applying_latest_migrations", maintenance=True)
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
            "manage.py",
            "migrate",
            "--noinput",
            "--skip-checks",
        ],
        timeout=900,
    )
    phase("reloading_all_game_template_data", maintenance=True)
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
            "manage.py",
            "bootstrap_game_data",
            "--skip-images",
            "--skip-avatar-assignment",
            "--verbosity",
            "1",
        ],
        timeout=1200,
    )
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
            "manage.py",
            "seed_work_templates",
            "--update-existing",
            "--verbosity",
            "1",
        ],
        timeout=180,
    )
    assert pending_migrations() == []
    preserve = app(
        """from guests.models import Guest,GuestSkill
from gameplay.models import BotRuntimeRoutingState,Manor
from django.core.serializers.json import DjangoJSONEncoder
print(json.dumps({'guest_ids':list(Guest.objects.order_by('id').values_list('id',flat=True)),
 'guest_skills':list(GuestSkill.objects.order_by('id').values('id','guest_id','skill_id')),
 'routing':list(BotRuntimeRoutingState.objects.order_by('key').values()),'manor_count':Manor.objects.count()},cls=DjangoJSONEncoder))
"""
    )
    for key in ("guest_ids", "guest_skills", "routing", "manor_count"):
        assert snapshot[key] == preserve[key], key + " changed during config sync"
    phase("collecting_latest_static_assets", maintenance=True)
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
            "manage.py",
            "collectstatic",
            "--noinput",
        ],
        timeout=600,
    )
    phase("starting_all_latest_services", maintenance=True)
    run(
        compose() + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", *SERVICES],
        timeout=420,
    )
    phase("waiting_for_readiness", maintenance=True)
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
    phase("verifying_every_runtime_file_on_all_services", maintenance=True)
    hash_code = (
        "import hashlib,json; from pathlib import Path; paths="
        + repr(list(M["files"]))
        + ';print(json.dumps({p:hashlib.sha256((Path("/app")/p).read_bytes()).hexdigest() for p in paths}))'
    )
    for s in SERVICES:
        name = "code-" + s + "-1"
        info = inspect(name)
        assert info["State"]["Running"] and info["Config"]["Image"] == M["image"], s
        assert info["HostConfig"]["Memory"] == before["services"][s]["memory"], s + " memory changed"
        actual = json.loads(run(["docker", "exec", "-i", name, "python", "-"], input=hash_code))
        assert actual == M["files"], s + " source mismatch"
    phase("synchronizing_host_runtime_sources", maintenance=True)
    with tarfile.open(P / "host-source-before.tar.gz", "w:gz") as archive:
        for name in M["files"]:
            source = ROOT / name
            if source.is_file():
                assert not source.is_symlink(), name + " is unexpectedly a symlink"
                archive.add(source, arcname=name, recursive=False)
    for name, digest in M["files"].items():
        destination = ROOT / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(P / "app" / name, destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    summary = battle_smoke(live=True)
    configs = app(
        """from gameplay.services.runtime_configs import reload_runtime_configs
from django.contrib.staticfiles.storage import staticfiles_storage
from trade.models import MarketListing,MarketTransaction
from gameplay.models import ItemTemplate,MissionTemplate,BuildingType,WorkTemplate
from guilds.models import GuildMissionTemplate
from battle.models import TroopTemplate
from gameplay.management.commands.load_item_templates import _build_item_defaults
from battle.management.commands.load_troop_templates import _build_troop_defaults
from guests.models import GuestTemplate
from guests.management.commands.load_guest_templates import Command
from django.conf import settings
from pathlib import Path
from io import StringIO
import yaml
c=Command(stdout=StringIO());s=c._build_sections(c._load_main_payload(Path(settings.BASE_DIR)/'data/guest_templates.yaml'),c._load_skills_payload(''),c._load_heroes_payload(''))
c._append_hero_roster_templates(s['template_data'],s['hero_roster'],s['attribute_profiles'],0)
fields=('name','rarity','archetype','base_attack','base_intellect','base_defense','base_agility','base_luck','base_hp')
for desired in s['template_data']:
    actual=GuestTemplate.objects.get(key=desired['key'])
    for field in fields:
        if field in desired:assert getattr(actual,field)==desired[field],(desired['key'],field)
data=Path(settings.BASE_DIR)/'data'
template_counts={}
for filename,section,model,builder in [
    ('item_templates.yaml','items',ItemTemplate,_build_item_defaults),
    ('troop_templates.yaml','troops',TroopTemplate,_build_troop_defaults),
]:
    entries=yaml.safe_load((data/filename).read_text())[section]
    for entry in entries:
        actual=model.objects.get(key=entry['key'])
        for field,value in builder(entry).items():
            assert getattr(actual,field)==value,(filename,entry['key'],field)
    template_counts[section]=len(entries)
for filename,section,model,fields in [
    ('mission_templates.yaml','missions',MissionTemplate,('name','description','enemy_guests','enemy_troops','enemy_technology','entry_cost','drop_table')),
    ('building_templates.yaml','buildings',BuildingType,('name','description','base_cost','base_upgrade_time','resource_type')),
    ('guild_mission_templates.yaml','missions',GuildMissionTemplate,('name','description','enemy_guests','enemy_troops','enemy_technology','entry_cost','drop_table')),
]:
    entries=yaml.safe_load((data/filename).read_text())[section]
    for entry in entries:
        actual=model.objects.get(key=entry['key'])
        for field in fields:
            if field in entry and hasattr(actual,field):assert getattr(actual,field)==entry[field],(filename,entry['key'],field)
    template_counts[filename]=len(entries)
assert WorkTemplate.objects.filter(key__in=['jiulou','yiguan','yizhan','shuyuan','chaguan','matou','wuguan','shanghang','guanfu','qianzhuang','biaoju','jingwumeng','shenfengyi','guozijian']).count()==14
assert MarketTransaction._meta.get_field('quantity')
assert not MarketTransaction._meta.get_field('listing').unique
print(json.dumps({'runtime_configs':reload_runtime_configs(),'guest_templates_verified':len(s['template_data']),'template_counts_verified':template_counts,'official_work_templates':14,'market_partial_purchase_schema':True,
 'static_urls':[staticfiles_storage.url('css/style.css'),staticfiles_storage.url('css/tailwind.css'),staticfiles_storage.url('js/trade.js')]}))
""",
        live=True,
    )
    summary.update(configs)
    summary.update(
        {
            "image": M["image"],
            "services": len(SERVICES),
            "runtime_files_verified_per_service": len(M["files"]),
            "player_guest_count_preserved": len(snapshot["guest_ids"]),
            "routing_preserved": True,
            "pending_migrations": 0,
            "memory_limits_preserved": True,
        }
    )
    (P / "verification.json").write_text(json.dumps(summary, indent=2))
    reload_proxy(original)
    phase("complete", maintenance=False)


def refresh_prepared():
    assert json.loads((P / "prepared.json").read_text())["image"] == M["image"]
    assert inspect("code-web-1")["Config"]["Image"] == M["expected_base_image"]
    assert hashlib.sha256((P / "release.tar.gz").read_bytes()).hexdigest() == M["archive_sha256"]
    with tarfile.open(P / "release.tar.gz") as archive:
        assert sorted(archive.getnames()) == sorted(M["files"])
        archive.extractall(P / "app", filter="data")
    for name, digest in M["files"].items():
        assert hashlib.sha256((P / "app" / name).read_bytes()).hexdigest() == digest
    phase("refreshing_tested_config_reload_image")
    run(["docker", "build", "-t", M["image"], str(P)], timeout=900)
    assert pending_migrations() == M["expected_pending_migrations"]
    template_preflight()
    result = battle_smoke()
    (P / "battle-image-smoke.json").write_text(json.dumps(result, indent=2))
    phase("prepared")


if __name__ == "__main__":
    with (ROOT / ".web-blue-green.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            {"prepare": prepare, "deploy": deploy, "refresh": refresh_prepared}[sys.argv[1]]()
        except Exception as error:
            phase("failed_" + type(error).__name__, message=str(error))
            raise
