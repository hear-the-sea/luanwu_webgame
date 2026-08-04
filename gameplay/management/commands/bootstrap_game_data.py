from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser

StepFunc = Callable[[], object]


class Command(BaseCommand):
    help = "一键加载核心游戏数据（建筑/科技/物品/兵种/门客/任务/帮会任务/打工）并刷新运行期配置。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--skip-images",
            action="store_true",
            help="跳过兵种/门客头像处理（适合 CI 或仅做数据同步时）。",
        )
        parser.add_argument(
            "--skip-config-reload",
            action="store_true",
            help="跳过商铺/拍卖/仓库/铁匠铺配置热加载。",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="某一步失败后继续执行后续步骤（最后输出失败摘要）。",
        )

    def _run_step(self, step_name: str, step_func: StepFunc, *, continue_on_error: bool, failures: list[str]) -> None:
        self.stdout.write(f"[START] {step_name}")
        try:
            step_func()
        except CommandError as exc:
            message = f"{step_name} 失败: {exc}"
            if continue_on_error:
                failures.append(message)
                self.stdout.write(self.style.WARNING(f"[FAIL] {message}"))
                return
            raise CommandError(message) from exc
        self.stdout.write(self.style.SUCCESS(f"[OK] {step_name}"))

    def _reload_runtime_configs(self) -> None:
        from gameplay.services.runtime_configs import format_runtime_config_summary, reload_runtime_configs

        summary = reload_runtime_configs()
        self.stdout.write(self.style.SUCCESS(f"[OK] 运行期配置已刷新: {format_runtime_config_summary(summary)}"))

    def handle(self, *args: object, **options: Any) -> None:
        skip_images = bool(options.get("skip_images"))
        skip_config_reload = bool(options.get("skip_config_reload"))
        continue_on_error = bool(options.get("continue_on_error"))
        verbosity = int(options.get("verbosity", 1) or 1)

        failures: list[str] = []

        def _call(name: str, **kwargs) -> StepFunc:
            return lambda: call_command(name, verbosity=verbosity, **kwargs)

        steps: list[tuple[str, StepFunc]] = [
            ("加载建筑模板", _call("load_building_templates")),
            ("加载科技模板", _call("load_technology_templates")),
            ("加载物品模板", _call("load_item_templates", repair_grain_ledger=True)),
            ("加载兵种模板", _call("load_troop_templates", skip_images=skip_images)),
            ("加载门客模板", _call("load_guest_templates", skip_images=skip_images)),
            ("加载任务模板", _call("load_mission_templates")),
            ("加载帮会任务模板", _call("load_guild_mission_templates")),
            ("初始化打工模板", _call("seed_work_templates")),
        ]

        if not skip_config_reload:
            steps.append(("刷新运行期配置", self._reload_runtime_configs))

        self.stdout.write("=== 开始一键加载游戏数据 ===")
        for step_name, step_func in steps:
            self._run_step(step_name, step_func, continue_on_error=continue_on_error, failures=failures)

        if failures:
            self.stdout.write(self.style.WARNING("=== 加载完成（存在失败项） ==="))
            for item in failures:
                self.stdout.write(self.style.WARNING(f"- {item}"))
            return

        self.stdout.write(self.style.SUCCESS("=== 一键加载完成 ==="))
