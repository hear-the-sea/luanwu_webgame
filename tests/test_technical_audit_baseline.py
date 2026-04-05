from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_DOC = PROJECT_ROOT / "docs" / "technical_audit_2026-03.md"
MAX_SHIM_LINES = 20
SHIM_PATHS = [
    PROJECT_ROOT / "tests" / "test_arena_views.py",
    PROJECT_ROOT / "tests" / "test_battle_report_view.py",
    PROJECT_ROOT / "tests" / "test_guild_mission_service.py",
    PROJECT_ROOT / "tests" / "test_guild_pvp_service.py",
    PROJECT_ROOT / "tests" / "test_guild_warehouse_service.py",
    PROJECT_ROOT / "tests" / "test_guilds_tasks.py",
    PROJECT_ROOT / "tests" / "test_load_guest_templates_command.py",
]


def _largest_test_files(limit: int) -> list[tuple[int, str]]:
    tests_root = PROJECT_ROOT / "tests"
    counts: list[tuple[int, str]] = []
    for path in tests_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        counts.append((line_count, str(path.relative_to(PROJECT_ROOT))))
    return sorted(counts, key=lambda item: (-item[0], item[1]))[:limit]


def test_recent_audit_entry_updates_document_header() -> None:
    header = AUDIT_DOC.read_text(encoding="utf-8").splitlines()[2]

    assert header == "最近更新：2026-04-05"


def test_split_test_entrypoints_remain_small_compatibility_shims() -> None:
    for path in SHIM_PATHS:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= MAX_SHIM_LINES, f"{path.name} should stay as a small compatibility shim"


def test_compatibility_shims_keep_matching_split_test_directories() -> None:
    tests_root = PROJECT_ROOT / "tests"

    for path in SHIM_PATHS:
        split_dir = tests_root / path.stem.removeprefix("test_")
        assert split_dir.is_dir(), f"{path.name} should keep matching split test directory {split_dir.name}"


def test_audit_doc_hot_test_baseline_matches_current_repo_state() -> None:
    audit_text = AUDIT_DOC.read_text(encoding="utf-8")

    for line_count, relative_path in _largest_test_files(limit=3):
        assert f"`{relative_path}`（`{line_count}` 行）" in audit_text

    assert "`tests/test_guild_mission_service.py`（`835` 行）" not in audit_text
    assert "`tests/test_arena_views.py`（`697` 行）" not in audit_text
    assert "`tests/test_battle_report_view.py`（`679` 行）" not in audit_text
    assert "`tests/test_guilds_tasks.py`（`690` 行）" not in audit_text
    assert "`tests/test_load_guest_templates_command.py`（`593` 行）" not in audit_text
