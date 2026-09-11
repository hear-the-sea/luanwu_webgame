"""Enforce the player-facing template interaction boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAYER_TEMPLATE_ROOTS = (
    Path("templates"),
    Path("battle/templates"),
    Path("gameplay/templates"),
    Path("guests/templates"),
    Path("guilds/templates"),
    Path("trade/templates"),
)

SCRIPT_BLOCK_RE = re.compile(
    r"<script\b(?P<attributes>[^>]*)>(?P<body>[\s\S]*?)</script\s*>",
    re.IGNORECASE,
)
SRC_ATTRIBUTE_RE = re.compile(r"(?<![-\w:])src\s*=", re.IGNORECASE)
INLINE_EVENT_ATTRIBUTE_RE = re.compile(r"(?<![-\w:])on[a-z][\w:-]*\s*=", re.IGNORECASE)


@dataclass(frozen=True)
class ContractViolation:
    path: str
    line: int
    kind: str
    detail: str


def iter_player_templates(project_root: Path = PROJECT_ROOT) -> Iterator[Path]:
    """Yield player-facing HTML templates in a stable order."""

    template_paths: set[Path] = set()
    for relative_root in PLAYER_TEMPLATE_ROOTS:
        root = project_root / relative_root
        if root.is_dir():
            template_paths.update(root.rglob("*.html"))
    yield from sorted(template_paths)


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _relative_path(path: Path, project_root: Path) -> str:
    return path.relative_to(project_root).as_posix()


def scan_player_templates(project_root: Path = PROJECT_ROOT) -> list[ContractViolation]:
    """Return violations of the player template interaction contract."""

    violations: list[ContractViolation] = []
    for relative_root in PLAYER_TEMPLATE_ROOTS:
        root = project_root / relative_root
        if not root.is_dir():
            violations.append(
                ContractViolation(
                    path=relative_root.as_posix(),
                    line=1,
                    kind="missing-template-root",
                    detail="player template root does not exist",
                )
            )

    for path in iter_player_templates(project_root):
        content = path.read_text(encoding="utf-8")
        relative_path = _relative_path(path, project_root)

        for match in SCRIPT_BLOCK_RE.finditer(content):
            if SRC_ATTRIBUTE_RE.search(match.group("attributes")) is None:
                violations.append(
                    ContractViolation(
                        path=relative_path,
                        line=_line_number(content, match.start()),
                        kind="inline-script",
                        detail="script blocks must load an external src",
                    )
                )

        for match in INLINE_EVENT_ATTRIBUTE_RE.finditer(content):
            violations.append(
                ContractViolation(
                    path=relative_path,
                    line=_line_number(content, match.start()),
                    kind="inline-event",
                    detail="event handlers must be registered by a page script",
                )
            )

    return sorted(violations, key=lambda item: (item.path, item.line, item.kind, item.detail))


def main() -> int:
    violations = scan_player_templates()
    if violations:
        print("Frontend template contract failed:")
        for violation in violations:
            print(f"  {violation.path}:{violation.line}: " f"{violation.kind}: {violation.detail}")
        return 1

    template_count = sum(1 for _ in iter_player_templates())
    print(f"Frontend template contract passed: {template_count} player templates checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
