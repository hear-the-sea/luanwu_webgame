from unittest.mock import Mock

import pytest

from scripts.deploy_web_blue_green import DeploymentError, DockerDeployment, apply_deployment


class FakeDeployment:
    def __init__(self, fail=None):
        self.events = []
        self.healthy = {"web": True, "candidate": False}
        self.proxy = "web"
        self.fail = fail

    def phase(self, name):
        self.events.append(("phase", name))

    def ready(self, target):
        return self.healthy[target]

    def create_candidate(self):
        self.events.append(("create",))
        self.healthy["candidate"] = self.fail != "candidate"

    def wait_ready(self, target, timeout):
        self.events.append(("ready", target))
        if not self.ready(target):
            raise DeploymentError("unready")

    def switch(self, target):
        assert self.ready(target), "routed to an unhealthy target"
        self.events.append(("switch", target))
        self.proxy = target

    def drain(self, seconds):
        assert seconds >= 65
        self.events.append(("drain", seconds))

    def replace_canonical(self):
        assert self.proxy == "candidate"
        self.events.append(("replace",))
        self.healthy["web"] = self.fail != "canonical"

    def cleanup_candidate(self):
        assert self.proxy != "candidate", "removed the live upstream"
        self.events.append(("cleanup",))


def test_healthy_transition_verifies_targets_and_drains_before_recreation():
    backend = FakeDeployment()
    apply_deployment(backend)
    operations = [event for event in backend.events if event[0] != "phase"]
    assert operations == [
        ("create",),
        ("ready", "candidate"),
        ("switch", "candidate"),
        ("drain", 65),
        ("replace",),
        ("ready", "web"),
        ("switch", "web"),
        ("drain", 65),
        ("cleanup",),
    ]


def test_candidate_failure_preserves_old_web_without_recreation():
    backend = FakeDeployment("candidate")
    with pytest.raises(DeploymentError):
        apply_deployment(backend)
    assert backend.proxy == "web"
    assert ("replace",) not in backend.events
    assert ("cleanup",) in backend.events


def test_canonical_failure_leaves_healthy_candidate_serving():
    backend = FakeDeployment("canonical")
    with pytest.raises(DeploymentError):
        apply_deployment(backend)
    assert backend.proxy == "candidate"
    assert ("cleanup",) not in backend.events
    assert ("phase", "failed_candidate_retained_serving") in backend.events
    assert ("switch", "web") not in backend.events


def test_initial_unhealthy_web_never_starts_a_candidate():
    backend = FakeDeployment()
    backend.healthy["web"] = False
    with pytest.raises(DeploymentError):
        apply_deployment(backend)
    assert ("create",) not in backend.events


def test_cleanup_refuses_a_container_owned_by_someone_else(monkeypatch):
    backend = object.__new__(DockerDeployment)
    backend.candidate = "project-web-bg-owned"
    backend.operation = "operation-owned"
    backend.inspect = lambda name: {"Config": {"Labels": {"webgame.blue-green.operation": "someone-else"}}}
    runner = Mock()
    monkeypatch.setattr("scripts.deploy_web_blue_green.command", runner)
    with pytest.raises(DeploymentError, match="not owned"):
        backend.cleanup_candidate()
    runner.assert_not_called()


def test_drain_cannot_be_shortened_below_http_timeout():
    with pytest.raises(DeploymentError, match="65"):
        apply_deployment(FakeDeployment(), drain_seconds=1)


def test_candidate_start_uses_no_public_ports_or_canonical_network_alias(monkeypatch, tmp_path):
    backend = object.__new__(DockerDeployment)
    backend.image = "webgame:already-built"
    backend.operation = "owned"
    backend.compose = ["docker", "compose", "-f", "live.yml"]
    backend.override = tmp_path / "override.json"
    backend.cwd = str(tmp_path)
    backend.candidate = "project-web-bg-owned"
    runner = Mock()
    monkeypatch.setattr("scripts.deploy_web_blue_green.command", runner)
    backend.create_candidate()
    argv = runner.call_args.args[0]
    assert argv[-6:] == ["run", "-d", "--no-deps", "--name", "project-web-bg-owned", "web"]
    assert "--service-ports" not in argv
    assert "--use-aliases" not in argv
    assert "build" not in argv


def test_failed_reload_restores_mounted_configuration_without_replacing_inode(monkeypatch, tmp_path):
    backend = object.__new__(DockerDeployment)
    backend.caddyfile = tmp_path / "Caddyfile"
    backend.original_config = "example.com {\n reverse_proxy web:8000\n}\n"
    backend.caddyfile.write_text(backend.original_config)
    inode = backend.caddyfile.stat().st_ino
    backend.caddy = "project-caddy"
    backend.candidate = "project-web-bg-owned"
    backend.proxy = "web"
    backend.ready = lambda target: True
    runner = Mock(side_effect=[DeploymentError("reload failed"), ""])
    monkeypatch.setattr("scripts.deploy_web_blue_green.command", runner)
    with pytest.raises(DeploymentError, match="reload failed"):
        backend.switch("candidate")
    assert backend.caddyfile.read_text() == backend.original_config
    assert backend.caddyfile.stat().st_ino == inode
    assert backend.proxy == "web"


def test_successful_cleanup_stops_and_removes_only_exact_owned_candidate(monkeypatch):
    backend = object.__new__(DockerDeployment)
    backend.candidate = "project-web-bg-owned"
    backend.operation = "owned"
    backend.inspect = lambda name: {"Config": {"Labels": {"webgame.blue-green.operation": "owned"}}}
    runner = Mock()
    monkeypatch.setattr("scripts.deploy_web_blue_green.command", runner)
    backend.cleanup_candidate()
    assert [call.args[0] for call in runner.call_args_list] == [
        ["docker", "stop", "--time", "65", backend.candidate],
        ["docker", "rm", backend.candidate],
    ]
