from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = (
    "web",
    "worker",
    "worker_battle",
    "worker_timer",
    "worker_timer_scan",
    "worker_timer_safety",
    "worker_timer_maintenance",
    "beat",
)


def test_runtime_redis_keeps_locks_and_capacity_keys_without_aof_or_public_ports():
    override = yaml.safe_load((ROOT / "docker-compose.redis-runtime.yml").read_text())
    runtime = override["services"]["redis_runtime"]
    command = runtime["command"][-1]
    assert "--appendonly no" in command
    assert '--save ""' in command
    assert "--maxmemory-policy noeviction" in command
    assert "ports" not in runtime
    assert "volumes" not in runtime
    assert runtime["mem_limit"] == "${REDIS_RUNTIME_MEMORY_LIMIT:-384m}"
    assert runtime["environment"]["REDIS_RUNTIME_MAXMEMORY"] == "${REDIS_RUNTIME_MAXMEMORY:-256mb}"
    for name in APPLICATIONS:
        service = override["services"][name]
        assert service["depends_on"]["redis_runtime"]["condition"] == "service_healthy"
        assert set(service["environment"]) == {"REDIS_CACHE_URL", "REDIS_CHANNEL_URL"}


@pytest.mark.skipif(not shutil.which("docker"), reason="Compose CLI is unavailable")
def test_real_compose_merge_routes_all_cache_writers_to_one_runtime_instance(tmp_path):
    env_file = tmp_path / "isolated.env"
    env_file.write_text(
        "\n".join(
            [
                "REDIS_PASSWORD=dummy-redis-password",
                "MYSQL_PASSWORD=dummy-mysql-password",
                "MYSQL_ROOT_PASSWORD=dummy-root-password",
                "CADDY_SITE_ADDRESS=example.invalid",
                "REDIS_BROKER_URL=redis://redis:6379/0",
                "REDIS_RESULT_URL=redis://redis:6379/0",
                "REDIS_CACHE_URL=redis://redis:6379/2",
                "REDIS_CHANNEL_URL=redis://redis:6379/1",
            ]
        )
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            "docker-compose.prod.yml",
            "-f",
            "docker-compose.redis-runtime.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "WEBGAME_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, "Isolated Compose config validation failed"
    services = json.loads(result.stdout)["services"]
    for name in APPLICATIONS:
        env = services[name]["environment"]
        assert env["REDIS_CACHE_URL"] == "redis://redis_runtime:6379/2"
        assert env["REDIS_CHANNEL_URL"] == "redis://redis_runtime:6379/1"
        assert env["REDIS_BROKER_URL"] == "redis://redis:6379/0"
        assert env["REDIS_RESULT_URL"] == "redis://redis:6379/0"
        assert services[name]["depends_on"]["redis_runtime"]["condition"] == "service_healthy"
    assert int(services["web"]["mem_limit"]) == 768 * 1024 * 1024
    assert "--appendonly yes" in services["redis"]["command"][-1]
