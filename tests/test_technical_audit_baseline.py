from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_SHIM_LINES = 20
SHIM_PATHS = [
    PROJECT_ROOT / "tests" / "test_arena_views.py",
    PROJECT_ROOT / "tests" / "test_battle_report_view.py",
    PROJECT_ROOT / "tests" / "test_guild_mission_views.py",
    PROJECT_ROOT / "tests" / "test_guild_mission_service.py",
    PROJECT_ROOT / "tests" / "test_guild_pvp_service.py",
    PROJECT_ROOT / "tests" / "test_guild_warehouse_service.py",
    PROJECT_ROOT / "tests" / "test_guilds_tasks.py",
    PROJECT_ROOT / "tests" / "test_load_guest_templates_command.py",
]


def test_split_test_entrypoints_remain_small_compatibility_shims() -> None:
    for path in SHIM_PATHS:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= MAX_SHIM_LINES, f"{path.name} should stay as a small compatibility shim"


def test_compatibility_shims_keep_matching_split_test_directories() -> None:
    tests_root = PROJECT_ROOT / "tests"

    for path in SHIM_PATHS:
        split_dir = tests_root / path.stem.removeprefix("test_")
        assert split_dir.is_dir(), f"{path.name} should keep matching split test directory {split_dir.name}"
