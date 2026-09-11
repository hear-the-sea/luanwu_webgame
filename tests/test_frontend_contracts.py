from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = PROJECT_ROOT / "scripts" / "check_frontend_contracts.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("frontend_contract_checker", CHECKER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_player_template_contract_passes_for_current_templates() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "player templates checked" in result.stdout


def test_player_template_contract_reports_inline_script_and_event(tmp_path: Path) -> None:
    checker = _load_checker()
    for relative_root in checker.PLAYER_TEMPLATE_ROOTS:
        (tmp_path / relative_root).mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "templates" / "contract.html"
    template_path.write_text(
        '<button data-onclick="safe">Broken</button>\n'
        '<button onclick="submitForm()">Broken</button>\n'
        "<script>window.inline = true;</script>\n",
        encoding="utf-8",
    )

    violations = checker.scan_player_templates(tmp_path)

    assert [(violation.kind, violation.line) for violation in violations] == [
        ("inline-event", 2),
        ("inline-script", 3),
    ]
