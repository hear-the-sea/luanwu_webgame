"""Web-only deployment. Planning is read-only; applying never builds or migrates."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4


class DeploymentError(RuntimeError):
    pass


def command(argv: list[str], *, cwd: str | None = None, timeout: int = 120) -> str:
    """Do not print resolved Compose environments or subprocess error output."""
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise DeploymentError(f"{argv[0]} command failed (exit {result.returncode}); inspect service logs separately")
    return result.stdout.strip()


class DockerDeployment:
    def __init__(self, web: str, image: str, phase_file: Path):
        self.web = web
        self.image = image
        self.phase_file = phase_file
        self.operation = uuid4().hex
        info = self.inspect(web)
        labels = info["Config"]["Labels"]
        self.project = labels["com.docker.compose.project"]
        self.cwd = labels["com.docker.compose.project.working_dir"]
        self.files = labels["com.docker.compose.project.config_files"].split(",")
        if (
            labels.get("com.docker.compose.service") != "web"
            or labels.get("com.docker.compose.oneoff", "False").lower() == "true"
        ):
            raise DeploymentError("--web-container must identify the canonical Compose web service")
        self.compose = ["docker", "compose", "--project-name", self.project, "--project-directory", self.cwd]
        for path in self.files:
            if not Path(path).is_file():
                raise DeploymentError("A live Compose configuration file is missing")
            self.compose.extend(["-f", path])
        for path in labels.get("com.docker.compose.project.environment_file", "").split(","):
            if path:
                self.compose.extend(["--env-file", path])
        self.caddy = self.service_container("caddy")
        caddy_info = self.inspect(self.caddy)
        self.caddyfile = Path(
            next(m["Source"] for m in caddy_info["Mounts"] if m["Destination"] == "/etc/caddy/Caddyfile")
        )
        self.original_config = self.caddyfile.read_text()
        if len(re.findall(r"(?m)^\s*reverse_proxy\s+web:8000\s*$", self.original_config)) != 1:
            raise DeploymentError("Expected exactly one reverse_proxy web:8000 directive")
        self.static_dir = Path(
            next(m["Source"] for m in caddy_info["Mounts"] if m["Destination"] == "/srv/staticfiles")
        )
        if not self.static_dir.is_dir():
            raise DeploymentError("The live static directory is missing")
        command(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        self.candidate = f"{self.project}-web-bg-{self.operation[:12]}"
        self.override = Path(self.cwd) / f".web-bg-{self.operation[:12]}.json"
        self.proxy = "web"

    def inspect(self, name: str) -> dict:
        return json.loads(command(["docker", "inspect", name]))[0]

    def service_container(self, service: str) -> str:
        containers = command(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--filter",
                f"label=com.docker.compose.service={service}",
                "--filter",
                "label=com.docker.compose.oneoff=False",
            ]
        ).splitlines()
        if len(containers) != 1:
            raise DeploymentError(f"Expected exactly one running canonical {service} container")
        return containers[0]

    def phase(self, phase: str):
        payload = {
            "operation": self.operation,
            "phase": phase,
            "candidate": self.candidate,
            "image": self.image,
            "proxy": self.proxy,
            "override": str(self.override),
            "updated_at": time.time(),
        }
        temporary = self.phase_file.with_suffix(self.phase_file.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2))
        temporary.replace(self.phase_file)
        print(json.dumps({"phase": phase, "candidate": self.candidate, "proxy": self.proxy}), flush=True)

    def create_candidate(self):
        self.override.write_text(
            json.dumps(
                {
                    "services": {
                        "web": {
                            "image": self.image,
                            "pull_policy": "never",
                            "environment": {
                                "DJANGO_RUN_MIGRATIONS": "0",
                                "DJANGO_SYNC_GUEST_TEMPLATES": "0",
                                "DJANGO_COLLECTSTATIC": "0",
                            },
                            "labels": {"webgame.blue-green.operation": self.operation},
                        }
                    }
                }
            )
        )
        self.compose.extend(["-f", str(self.override)])
        # `run` publishes no service ports unless --service-ports is supplied.
        # No --use-aliases: the candidate must never steal canonical web DNS.
        command(self.compose + ["run", "-d", "--no-deps", "--name", self.candidate, "web"], cwd=self.cwd)

    def ready(self, target: str) -> bool:
        try:
            name = self.service_container("web") if target == "web" else self.candidate
            info = self.inspect(name)
            if not info["State"]["Running"]:
                return False
            command(["docker", "exec", name, "python", "scripts/check_web_readiness.py"], timeout=20)
            return True
        except (DeploymentError, subprocess.TimeoutExpired):
            return False

    def wait_ready(self, target: str, seconds: int):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.ready(target):
                return
            time.sleep(2)
        raise DeploymentError(f"Readiness timeout: {target}")

    def switch(self, target: str):
        if not self.ready(target):
            raise DeploymentError("Refusing to route traffic to an unready target")
        upstream = "web" if target == "web" else self.candidate
        config = re.sub(r"(?m)^(\s*reverse_proxy\s+)web:8000\s*$", rf"\g<1>{upstream}:8000", self.original_config)
        previous_config = self.caddyfile.read_text()

        def write_config(content):
            # Preserve the inode used by the Caddy bind mount.
            with self.caddyfile.open("w") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())

        reload_command = [
            "docker",
            "exec",
            self.caddy,
            "caddy",
            "reload",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ]
        write_config(config)
        try:
            command(reload_command)
        except Exception:
            write_config(previous_config)
            try:
                command(reload_command)
            except Exception:
                self.phase("failed_proxy_reload_manual_recovery")
            raise
        self.proxy = target

    def drain(self, seconds: int):
        for _ in range(seconds):
            time.sleep(1)

    def replace_canonical(self):
        command(self.compose + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "web"], cwd=self.cwd)

    def cleanup_candidate(self):
        try:
            info = self.inspect(self.candidate)
        except DeploymentError:
            return
        if info["Config"]["Labels"].get("webgame.blue-green.operation") != self.operation:
            raise DeploymentError("Refusing to clean a container not owned by this operation")
        command(["docker", "stop", "--time", "65", self.candidate], timeout=90)
        command(["docker", "rm", self.candidate])

    def plan(self):
        return {
            "project": self.project,
            "compose_files": self.files,
            "image": self.image,
            "candidate": self.candidate,
            "caddyfile": str(self.caddyfile),
            "static_dir": str(self.static_dir),
            "steps": [
                "candidate ready",
                "proxy candidate",
                "drain 65s",
                "replace web only",
                "web ready",
                "proxy web",
                "owned candidate cleanup",
            ],
        }


def apply_deployment(backend, *, readiness_seconds: int = 180, drain_seconds: int = 65):
    if drain_seconds < 65:
        raise DeploymentError("HTTP drain must be at least 65 seconds")
    backend.phase("preflight")
    if not backend.ready("web"):
        raise DeploymentError("Current canonical web is not ready")
    created = False
    try:
        backend.phase("candidate_starting")
        created = True
        backend.create_candidate()
        backend.wait_ready("candidate", readiness_seconds)
        backend.switch("candidate")
        backend.phase("candidate_serving_old_http_draining")
        backend.drain(drain_seconds)
        backend.phase("canonical_replacing_candidate_serving")
        backend.replace_canonical()
        backend.wait_ready("web", readiness_seconds)
        backend.switch("web")
        backend.phase("canonical_serving_candidate_draining")
        backend.drain(drain_seconds)
        backend.cleanup_candidate()
        backend.phase("complete")
    except Exception:
        # Never restore an unready old/new canonical just to finish cleanup.
        # Reassert the healthy upstream even if the failed reload left the
        # on-disk Caddyfile different from Caddy's active runtime config.
        if backend.ready("web"):
            backend.switch("web")
            if created:
                backend.phase("failed_canonical_serving_candidate_draining")
                backend.drain(drain_seconds)
                backend.cleanup_candidate()
            backend.phase("failed_canonical_retained")
        elif created and backend.ready("candidate"):
            backend.switch("candidate")
            backend.phase("failed_candidate_retained_serving")
        else:
            backend.phase("failed_no_ready_target_manual_recovery")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-container", required=True)
    parser.add_argument("--image", required=True, help="Already-built local image")
    parser.add_argument("--phase-file", type=Path, default=Path("/tmp/web-blue-green-phase.json"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-migrations", action="store_true")
    parser.add_argument("--protocol-compatible", action="store_true")
    assets = parser.add_mutually_exclusive_group()
    assets.add_argument("--assets-unchanged", action="store_true")
    assets.add_argument(
        "--static-ready",
        action="store_true",
        help="Certify new assets AND old hashed assets coexist in the mounted static directory",
    )
    args = parser.parse_args()
    backend = DockerDeployment(args.web_container, args.image, args.phase_file)
    if not args.apply:
        print(json.dumps(backend.plan(), indent=2))
        return
    if not (args.no_migrations and args.protocol_compatible and (args.assets_unchanged or args.static_ready)):
        parser.error("--apply requires --no-migrations --protocol-compatible and an asset certification")
    # One deployer per Compose project. Never print rendered environment data.
    with open(Path(backend.cwd) / ".web-blue-green.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apply_deployment(backend)


if __name__ == "__main__":
    main()
