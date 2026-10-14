#!/usr/bin/env python3

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement


def _iter_root_requirements(requirements_file: Path, *, _seen_files: set[Path] | None = None) -> list[str]:
    _seen_files = _seen_files or set()
    resolved_file = requirements_file.resolve()
    if resolved_file in _seen_files:
        return []
    _seen_files.add(resolved_file)

    names: list[str] = []
    for raw_line in requirements_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r"):
            nested_path = line[2:].strip()
            if nested_path:
                names.extend(
                    _iter_root_requirements((requirements_file.parent / nested_path).resolve(), _seen_files=_seen_files)
                )
            continue
        if line.startswith("--requirement"):
            nested_path = line[len("--requirement") :].lstrip(" =")
            if nested_path:
                names.extend(
                    _iter_root_requirements((requirements_file.parent / nested_path).resolve(), _seen_files=_seen_files)
                )
            continue
        req = Requirement(line)
        names.append(str(req))
    return names


def _iter_root_names(requirements_file: Path) -> list[str]:
    return [Requirement(raw).name for raw in _iter_root_requirements(requirements_file)]


def _dist_for_name(name: str) -> metadata.Distribution:
    # importlib.metadata is case-insensitive, but normalize anyway.
    return metadata.distribution(name)


def _iter_deps(dist: metadata.Distribution, env: dict[str, str]) -> list[str]:
    out: list[str] = []
    for raw in dist.requires or []:
        try:
            req = Requirement(raw)
        except Exception:
            continue

        if req.marker and not req.marker.evaluate(env):
            continue
        out.append(req.name)
    return out


def main() -> int:
    env = default_environment()
    requirements_file = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else (Path(__file__).resolve().parent.parent / "requirements.txt")
    )

    pending = [Requirement(raw) for raw in _iter_root_requirements(requirements_file)]
    visited: set[tuple[str, frozenset[str]]] = set()
    pinned: dict[str, str] = {}
    extras: dict[str, set[str]] = {}

    while pending:
        req = pending.pop()
        if req.marker and not req.marker.evaluate(env):
            continue
        name = req.name
        key = (name.lower(), frozenset(req.extras))
        try:
            dist = _dist_for_name(name)
        except metadata.PackageNotFoundError:
            sys.stderr.write(f"Missing distribution for requirement: {name}\n")
            return 2

        if not req.specifier.contains(dist.version, prereleases=True):
            sys.stderr.write(f"Incompatible installed distribution: {req} (installed {dist.version})\n")
            return 2
        if key in visited:
            continue
        visited.add(key)
        pinned[dist.metadata["Name"]] = dist.version
        extras.setdefault(dist.metadata["Name"], set()).update(req.extras)
        for raw in dist.requires or []:
            dependency = Requirement(raw)
            if dependency.marker and not any(
                dependency.marker.evaluate({**env, "extra": extra}) for extra in {"", *req.extras}
            ):
                continue
            # The selected parent's extras have already been evaluated.
            dependency.marker = None
            pending.append(dependency)

    for name in sorted(pinned, key=lambda n: n.lower()):
        selected_extras = extras.get(name, set())
        suffix = f"[{','.join(sorted(selected_extras))}]" if selected_extras else ""
        sys.stdout.write(f"{name}{suffix}=={pinned[name]}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
