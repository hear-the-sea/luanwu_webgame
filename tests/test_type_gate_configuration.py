from __future__ import annotations

import tomllib
from pathlib import Path

TARGET_MODULES = {
    "gameplay.services.arena.coop_core",
    "gameplay.services.arena.coop_battle",
    "gameplay.services.arena.coop_lifecycle",
    "gameplay.services.arena.coop_settlement",
    "trade.services.auction.rounds",
    "trade.services.auction.rounds_lifecycle_support",
    "trade.services.auction.rounds_settlement_support",
    "trade.services.auction.rounds_delivery_support",
}


def _load_strict_mypy_modules() -> set[str]:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    strict_modules: set[str] = set()

    for override in config["tool"]["mypy"]["overrides"]:
        if override.get("disallow_untyped_defs") is not True:
            continue
        modules = override.get("module", [])
        if isinstance(modules, str):
            strict_modules.add(modules)
            continue
        strict_modules.update(str(module_name) for module_name in modules)

    return strict_modules


def test_strict_mypy_override_includes_recently_refactored_service_modules():
    strict_modules = _load_strict_mypy_modules()

    assert TARGET_MODULES.issubset(strict_modules)
