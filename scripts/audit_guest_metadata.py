from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

ALLOWED_GENDERS = {"male", "female", "unknown"}
FICTION_SOURCE_HINTS: dict[str, tuple[str, ...]] = {
    "gulong.yaml": ("古龙",),
    "jinyong.yaml": ("金庸",),
    "huangyi.yaml": ("黄易",),
    "liangyshen.yaml": ("梁羽生",),
    "wenruian.yaml": ("温瑞安",),
    "suitang.yaml": ("隋唐", "说唐", "隋唐演义"),
    "special.yaml": ("古龙", "金庸", "黄易", "温瑞安", "梁羽生", "历史"),
}
HISTORY_FILE_PREFIX = "history_"
SOURCE_PATTERN = re.compile(r"出自([^。\n；]+)")
SOURCE_TITLE_PATTERN = re.compile(r"《[^》]{1,80}》")
PLACEHOLDER_SNIPPETS = (
    "其事迹在史籍与地方文献中多有记载",
    "后世评述亦多，影响延续至今",
    "其相关事功在后世整理时常被引述",
)
LIMITED_SOURCE_SNIPPETS = (
    "史籍记载有限",
    "记载有限",
    "史载简略",
    "史载甚略",
    "传世记载零散",
    "传世材料不足",
    "现存材料不足",
    "难确考",
    "难以详考",
    "难以确考",
    "难以完整复原",
    "生平不详",
    "事迹不彰",
    "事迹罕传",
    "行迹难稽",
    "无从详考",
)
MIN_LIMITED_SOURCE_FLAVOR_LEN = 50
MIN_HISTORY_FLAVOR_LEN = 70


@dataclass(frozen=True)
class GuestRecord:
    file: str
    rarity: str
    index: int
    key: str
    name: str
    gender: str
    flavor: str
    archetype: str
    source_text: str
    batch_id: int


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    code: str
    file: str
    rarity: str
    key: str
    name: str
    batch_id: int
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit guest metadata in data/guests/*.yaml.")
    parser.add_argument("--guests-dir", default="data/guests", help="Directory containing guest roster YAML files.")
    parser.add_argument("--out-dir", default="reports/guest_metadata_audit", help="Directory for audit outputs.")
    parser.add_argument("--batch-size", type=int, default=200, help="Number of guests per audit batch.")
    parser.add_argument("--min-flavor-len", type=int, default=100, help="Minimum flavor length requirement.")
    return parser.parse_args()


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _extract_source_text(flavor: str) -> str:
    matched = SOURCE_PATTERN.search(flavor or "")
    if not matched:
        return ""
    return matched.group(1).strip()


def _source_text_matches(file_name: str, source_text: str, expected_hints: tuple[str, ...]) -> bool:
    if any(hint in source_text for hint in expected_hints):
        return True
    # special.yaml 允许仅使用作品名标注来源，例如「出自《笑傲江湖》」。
    if file_name == "special.yaml" and SOURCE_TITLE_PATTERN.search(source_text):
        return True
    return False


def _is_source_limited_history_flavor(record: GuestRecord) -> bool:
    if not record.file.startswith(HISTORY_FILE_PREFIX):
        return False
    if len(record.flavor) < MIN_LIMITED_SOURCE_FLAVOR_LEN:
        return False
    return any(snippet in record.flavor for snippet in LIMITED_SOURCE_SNIPPETS)


def _effective_min_flavor_len(record: GuestRecord, configured_min: int) -> int:
    if record.file.startswith(HISTORY_FILE_PREFIX):
        return min(configured_min, MIN_HISTORY_FLAVOR_LEN)
    return configured_min


def load_guest_records(guests_dir: Path, batch_size: int) -> list[GuestRecord]:
    records: list[GuestRecord] = []
    seq = 0

    for path in sorted(guests_dir.glob("*.yaml")):
        if path.name == "base.yaml":
            continue
        payload = _safe_load_yaml(path)
        heroes = payload.get("heroes")
        if not isinstance(heroes, dict):
            continue

        for rarity, rows in heroes.items():
            if not isinstance(rows, list):
                continue
            for idx, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                seq += 1
                batch_id = ((seq - 1) // max(1, batch_size)) + 1
                flavor = str(row.get("flavor") or "")
                records.append(
                    GuestRecord(
                        file=path.name,
                        rarity=str(rarity),
                        index=idx,
                        key=str(row.get("key") or "").strip(),
                        name=str(row.get("name") or "").strip(),
                        gender=str(row.get("default_gender") or "").strip(),
                        flavor=flavor.strip(),
                        archetype=str(row.get("archetype") or "").strip(),
                        source_text=_extract_source_text(flavor),
                        batch_id=batch_id,
                    )
                )
    return records


def _add_issue(
    issues: list[AuditIssue],
    *,
    severity: str,
    code: str,
    record: GuestRecord,
    detail: str,
) -> None:
    issues.append(
        AuditIssue(
            severity=severity,
            code=code,
            file=record.file,
            rarity=record.rarity,
            key=record.key,
            name=record.name,
            batch_id=record.batch_id,
            detail=detail,
        )
    )


def audit_records(records: Iterable[GuestRecord], min_flavor_len: int = 100) -> list[AuditIssue]:
    record_list = list(records)
    issues: list[AuditIssue] = []

    key_counter = Counter(record.key for record in record_list if record.key)
    for record in record_list:
        if not record.key:
            _add_issue(issues, severity="error", code="missing_key", record=record, detail="Guest key is empty.")
        if not record.name:
            _add_issue(issues, severity="error", code="missing_name", record=record, detail="Guest name is empty.")
        if record.gender not in ALLOWED_GENDERS:
            _add_issue(
                issues,
                severity="error",
                code="invalid_gender",
                record=record,
                detail=f"Unexpected gender value: {record.gender!r}.",
            )
        effective_min_flavor_len = _effective_min_flavor_len(record, min_flavor_len)
        if not record.flavor:
            _add_issue(issues, severity="error", code="missing_flavor", record=record, detail="Flavor text is empty.")
        elif len(record.flavor) < effective_min_flavor_len and not _is_source_limited_history_flavor(record):
            _add_issue(
                issues,
                severity="warning",
                code="short_flavor",
                record=record,
                detail=f"Flavor too short ({len(record.flavor)} chars, requires >= {effective_min_flavor_len}).",
            )

        if record.key and key_counter[record.key] > 1:
            _add_issue(issues, severity="error", code="duplicate_key", record=record, detail="Duplicate key detected.")

        male_count = record.flavor.count("他")
        female_count = record.flavor.count("她")
        if record.gender == "female" and male_count >= 3 and male_count > female_count * 2:
            _add_issue(
                issues,
                severity="warning",
                code="gender_pronoun_mismatch",
                record=record,
                detail=f"female but male pronouns dominate (他={male_count}, 她={female_count}).",
            )
        if record.gender == "male" and female_count >= 3 and female_count > male_count * 2:
            _add_issue(
                issues,
                severity="warning",
                code="gender_pronoun_mismatch",
                record=record,
                detail=f"male but female pronouns dominate (他={male_count}, 她={female_count}).",
            )

        for snippet in PLACEHOLDER_SNIPPETS:
            if snippet in record.flavor:
                _add_issue(
                    issues,
                    severity="warning",
                    code="placeholder_flavor_pattern",
                    record=record,
                    detail=f"Flavor contains templated phrase: {snippet}",
                )
                break

        if record.file.startswith(HISTORY_FILE_PREFIX):
            if "小说" in record.source_text:
                _add_issue(
                    issues,
                    severity="warning",
                    code="history_source_novel_like",
                    record=record,
                    detail=f"History file has novel-like source text: {record.source_text}",
                )
        else:
            expected_hints = FICTION_SOURCE_HINTS.get(record.file)
            if expected_hints:
                if not record.source_text:
                    _add_issue(
                        issues,
                        severity="warning",
                        code="missing_source_text",
                        record=record,
                        detail="Flavor text misses explicit source clause (e.g. 出自...).",
                    )
                elif not _source_text_matches(record.file, record.source_text, expected_hints):
                    _add_issue(
                        issues,
                        severity="warning",
                        code="source_hint_mismatch",
                        record=record,
                        detail=f"Source text {record.source_text!r} does not match expected hints {expected_hints!r}.",
                    )
    return issues


def write_outputs(out_dir: Path, records: list[GuestRecord], issues: list[AuditIssue]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    issues_csv = out_dir / "issues.csv"
    with issues_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["severity", "code", "batch_id", "file", "rarity", "key", "name", "detail"])
        for issue in sorted(issues, key=lambda x: (x.batch_id, x.file, x.key, x.code)):
            writer.writerow(
                [
                    issue.severity,
                    issue.code,
                    issue.batch_id,
                    issue.file,
                    issue.rarity,
                    issue.key,
                    issue.name,
                    issue.detail,
                ]
            )

    summary = build_summary(records, issues)
    summary_json = out_dir / "summary.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_md = out_dir / "summary.md"
    summary_md.write_text(render_summary_markdown(summary), encoding="utf-8")


def build_summary(records: list[GuestRecord], issues: list[AuditIssue]) -> dict[str, Any]:
    severity_counts = Counter(issue.severity for issue in issues)
    code_counts = Counter(issue.code for issue in issues)
    issues_by_batch: dict[int, int] = defaultdict(int)
    for issue in issues:
        issues_by_batch[issue.batch_id] += 1

    total_batches = max((record.batch_id for record in records), default=0)
    records_by_batch: dict[int, int] = defaultdict(int)
    for record in records:
        records_by_batch[record.batch_id] += 1

    batch_rows = []
    for batch_id in range(1, total_batches + 1):
        batch_rows.append(
            {
                "batch_id": batch_id,
                "guest_count": records_by_batch.get(batch_id, 0),
                "issue_count": issues_by_batch.get(batch_id, 0),
            }
        )

    return {
        "total_guests": len(records),
        "total_batches": total_batches,
        "total_issues": len(issues),
        "severity_counts": dict(severity_counts),
        "issue_code_counts": dict(code_counts),
        "batch_stats": batch_rows,
        "files_covered": sorted({record.file for record in records}),
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Guest Metadata Audit Summary")
    lines.append("")
    lines.append(f"- Total guests: {summary['total_guests']}")
    lines.append(f"- Total batches: {summary['total_batches']}")
    lines.append(f"- Total issues: {summary['total_issues']}")
    lines.append("")
    lines.append("## Severity Counts")
    lines.append("")
    for severity, count in sorted(summary["severity_counts"].items()):
        lines.append(f"- {severity}: {count}")
    lines.append("")
    lines.append("## Issue Code Counts")
    lines.append("")
    for code, count in sorted(summary["issue_code_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {code}: {count}")
    lines.append("")
    lines.append("## Batch Stats")
    lines.append("")
    lines.append("| Batch | Guests | Issues |")
    lines.append("|---:|---:|---:|")
    for row in summary["batch_stats"]:
        lines.append(f"| {row['batch_id']} | {row['guest_count']} | {row['issue_count']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    guests_dir = Path(args.guests_dir)
    out_dir = Path(args.out_dir)
    records = load_guest_records(guests_dir=guests_dir, batch_size=max(1, int(args.batch_size)))
    issues = audit_records(records, min_flavor_len=max(1, int(args.min_flavor_len)))
    write_outputs(out_dir=out_dir, records=records, issues=issues)
    print(
        json.dumps(
            {
                "records": len(records),
                "issues": len(issues),
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
