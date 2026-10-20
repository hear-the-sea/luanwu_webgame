"""
数据清理统计报表命令

用于查看各日志/战报表当前数据规模与按保留策略可清理数据量。

使用方式：
    python manage.py cleanup_data_report
    python manage.py cleanup_data_report --model gameplay.ResourceEvent
    python manage.py cleanup_data_report --days 7
    python manage.py cleanup_data_report --json
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Max, Min
from django.utils import timezone

from gameplay.record_retention import MAX_RECORD_RETENTION_DAYS, eligible_record_queryset
from gameplay.services.arena.fill_service_retention import (
    eligible_fill_service_episodes,
    fill_service_outcomes_for_retention,
)

from .cleanup_old_data import (
    ARENA_FILL_SERVICE_RETENTION_DAYS,
    CLEANUP_CONFIG,
    FILL_SERVICE_MODEL_PATHS,
    TERMINAL_CLEANUP_CONFIG,
)


class Command(BaseCommand):
    help = "输出数据清理统计报表（总量/可清理量/保留策略）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="覆盖保留天数，须非负；大于15天的值会限制为15天（默认使用各模型配置）",
        )
        parser.add_argument(
            "--model",
            type=str,
            default=None,
            help="只统计指定模型（如 gameplay.ResourceEvent）",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="以 JSON 格式输出报表",
        )

    def handle(self, *args: object, **options: Any) -> None:
        override_days = options["days"]
        if override_days is not None and override_days < 0:
            raise CommandError("days must be non-negative")
        if override_days is not None:
            override_days = min(MAX_RECORD_RETENTION_DAYS, int(override_days))
        target_model = options["model"]
        json_mode = bool(options["json"])

        now = timezone.now()
        rows: list[dict[str, Any]] = []
        totals = {
            "total_records": 0,
            "stale_records": 0,
            "models": 0,
        }

        if target_model is None or target_model in FILL_SERVICE_MODEL_PATHS:
            fill_rows = self._build_fill_service_rows(
                days=override_days if override_days is not None else ARENA_FILL_SERVICE_RETENTION_DAYS,
                target_model=target_model,
                now=now,
            )
            rows.extend(fill_rows)
            for row in fill_rows:
                totals["models"] += 1
                totals["total_records"] += int(row["total_records"])
                totals["stale_records"] += int(row["stale_records"])

        for model_path, (default_days, time_field) in CLEANUP_CONFIG.items():
            if target_model and model_path != target_model:
                continue

            days = override_days if override_days is not None else default_days
            row = self._build_model_row(model_path=model_path, days=days, time_field=time_field, now=now)
            rows.append(row)
            if row.get("status") == "ok":
                totals["models"] += 1
                totals["total_records"] += int(row["total_records"])
                totals["stale_records"] += int(row["stale_records"])

        for model_path, (default_days, time_field) in TERMINAL_CLEANUP_CONFIG.items():
            if target_model and model_path != target_model:
                continue

            days = override_days if override_days is not None else default_days
            row = self._build_model_row(
                model_path=model_path,
                days=days,
                time_field=time_field,
                now=now,
                terminal_only=True,
            )
            rows.append(row)
            if row.get("status") == "ok":
                totals["models"] += 1
                totals["total_records"] += int(row["total_records"])
                totals["stale_records"] += int(row["stale_records"])

        report = {
            "generated_at": now.isoformat(),
            "override_days": override_days,
            "target_model": target_model,
            "totals": totals,
            "rows": rows,
        }

        if json_mode:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
            return

        self._print_human_report(report)

    def _build_fill_service_rows(self, *, days: int, target_model: str | None, now: datetime) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=days)
        episodes = eligible_fill_service_episodes(cutoff=cutoff)
        outcomes = fill_service_outcomes_for_retention(cutoff=cutoff)
        rows: list[dict[str, Any]] = []
        if target_model in (None, "gameplay.ArenaFillServiceEpisode"):
            rows.append(
                self._build_retention_row(
                    "gameplay.ArenaFillServiceEpisode",
                    episodes,
                    days,
                    "last_observed_at",
                    now=now,
                )
            )
        if target_model in (None, "gameplay.ArenaFillServiceOutcome"):
            rows.append(
                self._build_retention_row(
                    "gameplay.ArenaFillServiceOutcome",
                    outcomes,
                    days,
                    "published_at",
                    now=now,
                )
            )
        return rows

    def _build_retention_row(
        self,
        model_path: str,
        queryset,
        days: int,
        time_field: str,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        model = queryset.model
        total_records = int(model.objects.count())
        stale_records = int(queryset.count())
        window = queryset.aggregate(oldest=Min(time_field), newest=Max(time_field))
        oldest = window.get("oldest")
        newest = window.get("newest")
        return {
            "model": model_path,
            "status": "ok",
            "retention_days": int(days),
            "time_field": time_field,
            "cutoff": (now - timedelta(days=days)).isoformat(),
            "total_records": total_records,
            "stale_records": stale_records,
            "stale_ratio": round((stale_records / total_records) if total_records else 0.0, 4),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
        }

    def _build_model_row(
        self,
        *,
        model_path: str,
        days: int,
        time_field: str,
        now: datetime,
        terminal_only: bool = False,
    ) -> dict[str, Any]:
        try:
            model = self._get_model(model_path)
        except (LookupError, ModuleNotFoundError) as exc:
            return {
                "model": model_path,
                "status": "error",
                "error": str(exc),
                "retention_days": days,
                "time_field": time_field,
            }

        cutoff = now - timedelta(days=days)
        queryset = (
            eligible_record_queryset(model_path, cutoff=cutoff)
            if terminal_only
            else model.objects.all().filter(**{f"{time_field}__lt": cutoff})
        )

        total_records = int(model.objects.count())
        stale_records = int(queryset.count())
        window = model.objects.aggregate(oldest=Min(time_field), newest=Max(time_field))
        oldest = window.get("oldest")
        newest = window.get("newest")

        ratio = (stale_records / total_records) if total_records > 0 else 0.0

        return {
            "model": model_path,
            "status": "ok",
            "retention_days": int(days),
            "time_field": time_field,
            "cutoff": cutoff.isoformat(),
            "total_records": total_records,
            "stale_records": stale_records,
            "stale_ratio": round(ratio, 4),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
        }

    def _print_human_report(self, report: dict[str, Any]) -> None:
        self.stdout.write("=== 数据清理统计报表 ===")
        self.stdout.write(f"生成时间: {report['generated_at']}")
        if report.get("target_model"):
            self.stdout.write(f"过滤模型: {report['target_model']}")
        if report.get("override_days") is not None:
            self.stdout.write(f"全局保留天数覆盖: {report['override_days']}")
        self.stdout.write("")

        for row in report["rows"]:
            if row.get("status") != "ok":
                self.stdout.write(self.style.WARNING(f"[{row['model']}] 统计失败: {row.get('error', 'unknown error')}"))
                continue

            self.stdout.write(
                (
                    f"[{row['model']}] 总量={row['total_records']} "
                    f"可清理={row['stale_records']} "
                    f"比例={row['stale_ratio']:.2%} "
                    f"保留={row['retention_days']}天 "
                    f"截止线={row['cutoff']}"
                )
            )
            self.stdout.write(
                f"    时间范围: oldest={row['oldest'] or '-'} newest={row['newest'] or '-'} field={row['time_field']}"
            )

        totals = report["totals"]
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"汇总: models={totals['models']} total_records={totals['total_records']} stale_records={totals['stale_records']}"
            )
        )

    def _get_model(self, model_path: str) -> Any:
        app_label, model_name = model_path.rsplit(".", 1)
        return apps.get_model(app_label, model_name)
