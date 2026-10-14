"""Dependency locks must enforce the Redis transport's supported versions."""

from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from scripts import generate_requirements_lock as lock_generator

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename", ["requirements.lock.txt", "requirements-dev.lock.txt"])
def test_locked_daphne_keeps_twisted_tls_extra_and_its_dependencies(filename):
    requirements = {
        canonicalize_name(req.name): req
        for raw in (ROOT / filename).read_text().splitlines()
        if raw.strip() and not raw.startswith("#")
        for req in [Requirement(raw)]
    }
    assert "tls" in requirements["twisted"].extras
    environment = {**default_environment(), "extra": "tls"}
    for raw in metadata.distribution("twisted").requires or []:
        dependency = Requirement(raw)
        if dependency.marker and not dependency.marker.evaluate(environment):
            continue
        assert canonicalize_name(dependency.name) in requirements
        locked = requirements[canonicalize_name(dependency.name)]
        assert dependency.specifier.contains(next(iter(locked.specifier)).version)


@pytest.mark.parametrize("filename", ["requirements.lock.txt", "requirements-dev.lock.txt"])
def test_locked_redis_is_supported_by_celery_transport(filename):
    requirements = {
        req.name.lower(): req
        for raw in (ROOT / filename).read_text().splitlines()
        if raw.strip() and not raw.startswith("#")
        for req in [Requirement(raw)]
    }
    assert "redis" in requirements["celery"].extras
    assert "redis" in requirements["kombu"].extras
    redis_version = next(iter(requirements["redis"].specifier)).version
    transport_requirements = [
        Requirement(raw) for raw in metadata.distribution("kombu").requires or [] if Requirement(raw).name == "redis"
    ]
    assert transport_requirements
    assert all(req.specifier.contains(redis_version) for req in transport_requirements)


def distribution(name, version, requires=()):
    return SimpleNamespace(metadata={"Name": name}, version=version, requires=requires)


def run_generator(monkeypatch, tmp_path, requirements, distributions):
    root = tmp_path / "requirements.txt"
    root.write_text(requirements)
    monkeypatch.setattr(lock_generator.sys, "argv", ["generate_requirements_lock.py", str(root)])
    monkeypatch.setattr(lock_generator, "_dist_for_name", distributions.__getitem__)
    return lock_generator.main()


def test_lock_generator_keeps_transitive_extras(monkeypatch, tmp_path, capsys):
    status = run_generator(
        monkeypatch,
        tmp_path,
        "celery[redis]>=5.5\n",
        {
            "celery": distribution("celery", "5.5.3", ['kombu[redis]; extra == "redis"']),
            "kombu": distribution("kombu", "5.5.4", ['redis<=5.2.1; extra == "redis"']),
            "redis": distribution("redis", "5.2.1"),
        },
    )
    assert status == 0
    assert capsys.readouterr().out == "celery[redis]==5.5.3\nkombu[redis]==5.5.4\nredis==5.2.1\n"


def test_lock_generator_rejects_incompatible_extra_dependency(monkeypatch, tmp_path, capsys):
    status = run_generator(
        monkeypatch,
        tmp_path,
        "kombu[redis]\n",
        {
            "kombu": distribution("kombu", "5.5.4", ['redis<=5.2.1; extra == "redis"']),
            "redis": distribution("redis", "6.4.0"),
        },
    )
    assert status == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Incompatible installed distribution" in captured.err


def test_lock_generator_validates_each_constraint_even_if_already_visited(monkeypatch, tmp_path, capsys):
    status = run_generator(
        monkeypatch,
        tmp_path,
        "redis<6\nredis>=4\n",
        {
            "redis": distribution("redis", "6.4.0"),
        },
    )
    assert status == 2
    assert "redis<6" in capsys.readouterr().err
