from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _lock_targets_in_order(function_name: str) -> list[str]:
    module = ast.parse(_read("guests/services/equipment.py"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == function_name)

    targets: list[str] = []
    for statement in function.body:
        if not isinstance(statement, ast.Assign):
            continue
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            continue
        rendered = ast.unparse(statement.value)
        if "select_for_update()" not in rendered:
            continue
        targets.append(statement.targets[0].id)
    return targets


def test_unequip_guest_item_locks_manor_before_guest_and_gear():
    assert _lock_targets_in_order("unequip_guest_item")[:3] == [
        "locked_manor",
        "guest",
        "gear",
    ]


def test_equip_guest_locks_manor_before_guest():
    assert _lock_targets_in_order("equip_guest")[:2] == ["locked_manor", "locked_guest"]
