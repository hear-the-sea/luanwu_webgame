from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_DOC = PROJECT_ROOT / "docs" / "technical_audit_2026-03.md"
COMPATIBILITY_INVENTORY_DOC = PROJECT_ROOT / "docs" / "compatibility_inventory_2026-03.md"
OPTIMIZATION_PLAN_DOC = PROJECT_ROOT / "docs" / "optimization_plan.md"
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

    assert header == "最近更新：2026-09-25"


def test_recent_audit_entry_records_gate_verification_summary() -> None:
    audit_text = AUDIT_DOC.read_text(encoding="utf-8")
    optimization_text = OPTIMIZATION_PLAN_DOC.read_text(encoding="utf-8")

    assert "2026-09-10 最终本地 hermetic 验证通过" in audit_text
    assert "`5713 passed, 234 skipped, 618 deselected`" in audit_text
    assert "在受限沙箱权限下访问本机 MySQL/Redis 的预检会失败" in audit_text
    assert "`DJANGO_TEST_USE_ENV_SERVICES=1 make test-critical` 通过 `79 passed`" in audit_text
    assert "Gate D1 通过 `393` 条契约测试、`10` 条核心真实服务测试和 `4` 条 Arena 相邻真实服务测试" in audit_text
    assert "完整 `integration` marker 套件在真实 MySQL/Redis 上通过" in audit_text
    assert "`120 passed, 7 skipped, 6435 deselected`" in audit_text
    assert "玩家侧帮会与交易模板的内联脚本/事件属性已清零" in audit_text
    assert "窗口级 `connection.execute_wrapper` 收集器" in audit_text
    assert "最大 `[1000]` 档复测 `1 passed in 158.40s` 且无 warning" in audit_text
    assert "开发依赖原有 `5` 个漏洞（`3 high / 1 moderate / 1 low`）" in audit_text
    assert "`npm audit fix --package-lock-only --registry=https://registry.npmjs.org`" in audit_text
    assert "复核结果为 `0 vulnerabilities`" in audit_text
    assert "前端语义门禁已补齐" in audit_text
    assert "`npm run lint:js`" in audit_text
    assert "`npm run test:browser` 在受控环境通过 `1 passed`" in audit_text
    assert "本机 `.env` 权限仍为 `644`" in audit_text
    assert "已在 settings 中补充生产 fail-closed 检查" in audit_text
    assert '直接运行 `pytest -m "not integration"` 会额外加载历史 commit-bound evidence 工件' in audit_text
    assert "不能只手改 count/checksum" in audit_text
    assert (
        "`npm run test:js`、`npm run check:js`、`npm run lint:js`、Playwright 浏览器回归与 `scripts/check_frontend_contracts.py`"
        in audit_text
    )
    assert "`scripts/check_frontend_contracts.py`" in audit_text
    assert "模板契约扫描已由 `make static-check` 与 CI 执行" in audit_text
    assert "当前最高优先级主题已调整为“前端工程化验证与长期边界维护”" in optimization_text
    assert (
        "持续执行 `npm run test:js`、`npm run check:js`、`npm run lint:js`、`npm run test:browser`" in optimization_text
    )
    assert "先处理 `templates/base.html` 的伪状态 / 伪入口问题" not in optimization_text
    assert "`trade/templates/trade/partials/_market.html`（`500` 行）" in audit_text
    assert "`battle_debugger/templates/battle_debugger/custom_config.html` 已将页面级样式迁出" in audit_text
    assert "`battle_debugger/templates/battle_debugger/custom_config.html` 的页面级样式已迁出" in optimization_text
    assert "当前模板为 `448` 行" in audit_text
    assert "当前模板为 `448` 行" in optimization_text
    assert "2026-05-06 `make lint` 通过" in audit_text
    assert '`python -m pytest -m "not integration" -q` 通过' in audit_text
    assert (
        "`python -m pytest -q tests/test_real_service_preflight.py tests/test_type_gate_configuration.py tests/test_guild_mission_views.py tests/test_technical_audit_baseline.py tests/test_guild_pvp_views.py tests/test_runtime_refresh_views.py tests/test_guild_hero_pool.py tests/test_guild_hero_pool_views.py tests/test_deployment_configuration.py tests/test_pytest_configuration.py tests/test_reload_runtime_configs_command.py` 通过"
        in audit_text
    )
    assert (
        "结果分别为 `JS gate + flake8 + mypy（563 source files）通过`、`2992 passed, 44 deselected` 与 `97 passed`"
        in audit_text
    )
    assert "预检提示已明确指向 `make test-real-services-up`" in audit_text


def test_split_test_entrypoints_remain_small_compatibility_shims() -> None:
    for path in SHIM_PATHS:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= MAX_SHIM_LINES, f"{path.name} should stay as a small compatibility shim"


def test_compatibility_shims_keep_matching_split_test_directories() -> None:
    tests_root = PROJECT_ROOT / "tests"

    for path in SHIM_PATHS:
        split_dir = tests_root / path.stem.removeprefix("test_")
        assert split_dir.is_dir(), f"{path.name} should keep matching split test directory {split_dir.name}"


def test_compatibility_inventory_records_current_public_shims() -> None:
    inventory_text = COMPATIBILITY_INVENTORY_DOC.read_text(encoding="utf-8")

    assert "`battle/simulation_core.py`" in inventory_text
    assert "`gameplay/models/__init__.py`" in inventory_text
    assert "`guilds/models/__init__.py`" in inventory_text
    assert "`guests/services/recruitment.py`" in inventory_text
    assert "`gameplay/services/technology.py`" in inventory_text


def test_compatibility_inventory_assigns_ownership_and_review_deadlines() -> None:
    inventory_text = COMPATIBILITY_INVENTORY_DOC.read_text(encoding="utf-8")
    sections = inventory_text.split("### `")[1:]

    assert sections
    for section in sections:
        assert "- 责任人：" in section
        assert "- 外部消费者登记：" in section
        assert "- 下次复核日期：" in section


def test_audit_doc_hot_test_baseline_matches_current_repo_state() -> None:
    audit_text = AUDIT_DOC.read_text(encoding="utf-8")

    for line_count, relative_path in _largest_test_files(limit=3):
        assert f"`{relative_path}`（`{line_count}` 行）" in audit_text

    assert "`tests/test_guild_mission_service.py`（`835` 行）" not in audit_text
    assert "`tests/test_arena_views.py`（`697` 行）" not in audit_text
    assert "`tests/test_battle_report_view.py`（`679` 行）" not in audit_text
    assert "`tests/test_guilds_tasks.py`（`690` 行）" not in audit_text
    assert "`tests/test_load_guest_templates_command.py`（`593` 行）" not in audit_text
