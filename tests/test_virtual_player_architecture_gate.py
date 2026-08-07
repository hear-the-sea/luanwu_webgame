from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

from tests.helpers.model_dml_audit import find_model_dml, iter_python_sources, source_imports_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GAMEPLAY_ROOT = PROJECT_ROOT / "gameplay"
RUNTIME_SOURCE_ROOTS = tuple(
    PROJECT_ROOT / name
    for name in (
        "accounts",
        "battle",
        "common",
        "config",
        "core",
        "gameplay",
        "guests",
        "guilds",
        "scripts",
        "tasks",
        "trade",
        "websocket",
    )
)

VIRTUAL_PLAYER_FACADE_EXPORTS = {
    "AcceleratedGrowthOutcome",
    "BotProjectionConfig",
    "PopulationMutationResult",
    "PopulationMutationStatus",
    "accelerate_virtual_player_growth",
    "clear_virtual_player_config_cache",
    "create_virtual_player",
    "create_virtual_player_with_capacity",
    "create_virtual_players_for_band",
    "get_virtual_player_capacity",
    "load_virtual_player_config",
    "maintain_due_virtual_players",
    "plan_virtual_player_population",
    "reactivate_retired_virtual_player_with_capacity",
    "reactivate_virtual_player_profile",
    "request_virtual_player_backfill_for_region_search",
    "retire_virtual_player_if_unprotected",
    "roll_virtual_player_population",
    "virtual_player_prestige_bands",
}

VIRTUAL_RESERVE_FACADE_EXPORTS = {
    "create_due_virtual_reserve_profiles",
    "fill_due_coop_reserve",
    "fill_due_tournament_reserve",
    "grow_due_virtual_reserves",
    "queue_virtual_reserve_reconcile",
    "reconcile_coop_demand",
    "reconcile_coop_demand_locked",
    "reconcile_tournament_demand",
    "reconcile_tournament_demand_locked",
    "replenish_virtual_reserve",
    "scan_virtual_reserve_demands",
}

VIRTUAL_PLAYER_RULES_COMPAT_EXPORTS = {
    "DEFAULT_COMBAT_PERSONAS",
    "STRENGTH_QUANTILES",
    "LifecycleDates",
    "apply_combat_persona",
    "apply_stable_troop_variation",
    "bounded_approach",
    "choose_lifecycle",
    "choose_strength_quantile",
    "nearest_rank_quantile",
}

PURE_VIRTUAL_PLAYER_RULE_OWNERS = (
    "gameplay/services/virtual_player_core/calibration.py",
    "gameplay/services/virtual_player_core/lifecycle.py",
    "gameplay/services/virtual_player_core/legacy/projection.py",
    "gameplay/services/virtual_player_core/maintenance_rules.py",
    "gameplay/services/virtual_player_core/projection.py",
)

READ_ONLY_VIRTUAL_PLAYER_OWNERS = (
    "gameplay/services/virtual_player_core/reference_snapshots.py",
    "gameplay/services/virtual_player_core/selectors.py",
    "gameplay/services/arena/virtual_protection.py",
)

BOT_PROFILE_DIRECT_IMPORTS = {
    "gameplay/admin/bots.py": "read-only display, filters, and Admin type metadata",
    "gameplay/management/commands/audit_virtual_player_baseline.py": "read-only deterministic baseline sampling",
    "gameplay/management/commands/generate_virtual_players.py": "Archetype choices only",
    "gameplay/services/arena/virtual_backfill.py": "typed arena backfill lineup contract",
    "gameplay/services/arena/virtual_reserve_fill.py": "reserve candidate reads and row locks",
    "gameplay/services/arena/virtual_reserve_pool.py": "reserve candidate reads, row locks, and state checks",
    "gameplay/services/jail.py": "read-only virtual captor selection for bounded daily cleanup",
    "gameplay/services/raid/utils.py": "read-only attack eligibility",
    "gameplay/services/virtual_player_core/bootstrap.py": "bootstrap profile contract and Archetype defaults",
    "gameplay/services/virtual_player_core/external_reconciliation.py": "reconciliation profile selection and locking boundary",
    "gameplay/services/virtual_player_core/legacy/inventory.py": "legacy inventory policy and typed profile input",
    "gameplay/services/virtual_player_core/legacy/roster.py": "legacy roster policy and maintained-state reads",
    "gameplay/services/virtual_player_core/maintenance.py": "maintenance selection, locking, and lifecycle boundary",
    "gameplay/services/virtual_player_core/population_runtime.py": "population selection and locking before delegated writes",
    "gameplay/services/virtual_player_core/policy_registry.py": "read-only policy retirement reference checks",
    "gameplay/services/virtual_player_core/profile_store.py": "target BotProfile write owner",
    "gameplay/services/virtual_player_core/reference_snapshots.py": "reference cohort reads and Archetype policy",
    "gameplay/services/virtual_player_core/selectors.py": "read-only profile selectors and relation predicates",
    "gameplay/services/virtual_player_loot_limits.py": "read-only loot cap decision",
}

BOT_PROFILE_RELATION_READERS = {
    "gameplay/management/commands/audit_virtual_player_baseline.py": "exclude virtual profiles from real samples",
    "gameplay/selectors/stats.py": "exclude virtual profiles from real-player activity counts",
    "gameplay/services/raid/map_search.py": "apply virtual-profile map visibility states",
    "gameplay/services/ranking.py": "exclude virtual profiles from real-player rankings",
    "gameplay/services/virtual_player_core/legacy/inventory.py": "scope inventory references to maintained profiles",
    "gameplay/services/virtual_player_core/legacy/roster.py": "scope roster references to maintained profiles",
    "gameplay/services/virtual_player_core/population_runtime.py": "separate real activity from virtual population",
    "gameplay/services/virtual_player_core/reference_snapshots.py": "exclude virtual profiles from real reference cohorts",
    "gameplay/services/virtual_player_core/selectors.py": "identify real manors and virtual profiles",
    "gameplay/services/virtual_player_loot_limits.py": "identify Raid runs against virtual defenders",
    "gameplay/views/map.py": "apply virtual-profile map visibility states",
    "guests/tasks.py": "exclude virtual profiles from real-player automatic training",
}

BOT_PROFILE_GATE_A_WRITE_OWNERS = {"gameplay/services/virtual_player_core/profile_store.py"}

ARENA_DEMAND_GATE_A_WRITE_OWNERS = {
    "gameplay/services/arena/virtual_reserve_demand.py",
    "gameplay/services/arena/virtual_reserve_fill.py",
    "gameplay/services/arena/virtual_reserve_pool.py",
}
ARENA_MEMBER_GATE_A_WRITE_OWNERS = {
    "gameplay/services/arena/virtual_reserve_fill.py",
    "gameplay/services/arena/virtual_reserve_pool.py",
}


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(PROJECT_ROOT).with_suffix("").parts)


def _resolve_import(module_name: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    parts = module_name.split(".")[: -node.level]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _imported_modules(path: Path, tree: ast.Module) -> set[str]:
    module_name = _module_name(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(_resolve_import(module_name, node))
    return modules


def _assert_framework_independent(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = _imported_modules(path, tree)
    assert not any(
        module == "django"
        or module.startswith("django.")
        or module == "gameplay.models"
        or module.startswith("gameplay.models.")
        for module in imported_modules
    )


def _assert_thin_facade(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) for node in ast.walk(tree))
    _assert_framework_independent(path)


def _gameplay_model_imports(path: Path, tree: ast.Module) -> set[str]:
    module_name = _module_name(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_module = _resolve_import(module_name, node)
        if imported_module == "gameplay.models" or imported_module.startswith("gameplay.models."):
            names.update(alias.name for alias in node.names if alias.name != "*")
    return names


def _assert_model_read_only(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for model_name in _gameplay_model_imports(path, tree):
        assert (
            find_model_dml(
                source,
                model_name=model_name,
                filename=str(path),
            )
            == ()
        )


def _iter_runtime_python_sources() -> Iterable[Path]:
    for root in RUNTIME_SOURCE_ROOTS:
        if root.exists():
            yield from iter_python_sources(root)


def _facade_imports(facade_module: str) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    for path in _iter_runtime_python_sources():
        module_name = _module_name(path)
        names: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or _resolve_import(module_name, node) != facade_module:
                continue
            names.update(alias.name for alias in node.names)
        if names:
            consumers[str(path.relative_to(PROJECT_ROOT))] = names
    return consumers


def _direct_model_import_paths(model_name: str) -> set[str]:
    paths = set()
    for path in _iter_runtime_python_sources():
        source = path.read_text(encoding="utf-8")
        if source_imports_model(source, model_name=model_name, filename=str(path)):
            paths.add(str(path.relative_to(PROJECT_ROOT)))
    return paths


def _runtime_symbol_reference_paths(symbol: str) -> set[str]:
    paths: set[str] = set()
    for path in _iter_runtime_python_sources():
        relative_path = str(path.relative_to(PROJECT_ROOT))
        if relative_path == "gameplay/services/virtual_player_core/bootstrap.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            (isinstance(node, ast.Name) and node.id == symbol)
            or (isinstance(node, ast.Attribute) and node.attr == symbol)
            for node in ast.walk(tree)
        ):
            paths.add(relative_path)
    return paths


def _model_write_owners(model_name: str, *, relation_names: tuple[str, ...] = ()) -> set[str]:
    owners = set()
    for root in RUNTIME_SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in iter_python_sources(root, include_package_initializers=True):
            source = path.read_text(encoding="utf-8")
            if find_model_dml(
                source,
                model_name=model_name,
                filename=str(path),
                relation_names=relation_names,
            ):
                owners.add(str(path.relative_to(PROJECT_ROOT)))
    return owners


def _lookup_mentions_relation(value: str, relation_name: str) -> bool:
    return relation_name in str(value).split("__")


def _assert_acyclic_import_graph(
    nodes: set[str],
    edges: set[tuple[str, str]],
) -> None:
    remaining = set(nodes)
    while remaining:
        leaves = {
            node for node in remaining if not any(source == node and target in remaining for source, target in edges)
        }
        assert leaves, f"import cycle among {sorted(remaining)}"
        remaining.difference_update(leaves)


def _bot_profile_relation_reader_paths() -> set[str]:
    paths: set[str] = set()
    for path in _iter_runtime_python_sources():
        if "models" in path.relative_to(PROJECT_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "bot_profile":
                paths.add(str(path.relative_to(PROJECT_ROOT)))
                break
            if isinstance(node, ast.keyword) and node.arg and _lookup_mentions_relation(node.arg, "bot_profile"):
                paths.add(str(path.relative_to(PROJECT_ROOT)))
                break
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _lookup_mentions_relation(node.value, "bot_profile")
            ):
                paths.add(str(path.relative_to(PROJECT_ROOT)))
                break
    return paths


def test_virtual_player_facade_freezes_the_gate_a_public_contract() -> None:
    from gameplay.services import virtual_players

    assert set(virtual_players.__all__) == VIRTUAL_PLAYER_FACADE_EXPORTS
    assert len(virtual_players.__all__) == len(set(virtual_players.__all__)) == 19
    assert all(hasattr(virtual_players, name) for name in virtual_players.__all__)
    _assert_thin_facade(GAMEPLAY_ROOT / "services" / "virtual_players.py")


def test_virtual_player_facade_consumers_are_explicit_and_only_import_public_names() -> None:
    consumers = _facade_imports("gameplay.services.virtual_players")

    assert consumers == {}


def test_virtual_player_rule_consumers_use_the_real_owners() -> None:
    assert _facade_imports("gameplay.services.virtual_player_rules") == {}


def test_virtual_player_rules_compatibility_module_is_a_thin_explicit_reexport() -> None:
    from gameplay.services import virtual_player_rules
    from gameplay.services.virtual_player_core import lifecycle
    from gameplay.services.virtual_player_core.legacy import projection

    expected_owners = {
        "DEFAULT_COMBAT_PERSONAS": projection.DEFAULT_COMBAT_PERSONAS,
        "STRENGTH_QUANTILES": projection.STRENGTH_QUANTILES,
        "LifecycleDates": lifecycle.LifecycleDates,
        "apply_combat_persona": projection.apply_combat_persona,
        "apply_stable_troop_variation": projection.apply_stable_troop_variation,
        "bounded_approach": projection.bounded_approach,
        "choose_lifecycle": lifecycle.choose_lifecycle,
        "choose_strength_quantile": projection.choose_strength_quantile,
        "nearest_rank_quantile": projection.nearest_rank_quantile,
    }

    assert set(virtual_player_rules.__all__) == VIRTUAL_PLAYER_RULES_COMPAT_EXPORTS
    assert all(getattr(virtual_player_rules, name) is owner for name, owner in expected_owners.items())

    facade_path = GAMEPLAY_ROOT / "services" / "virtual_player_rules.py"
    tree = ast.parse(facade_path.read_text(encoding="utf-8"), filename=str(facade_path))
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in tree.body)


def test_virtual_player_core_package_root_does_not_aggregate_owner_symbols() -> None:
    from gameplay.services import virtual_player_core
    from gameplay.services.virtual_player_core import bootstrap

    assert not hasattr(virtual_player_core, "PopulationPlan")
    assert not hasattr(virtual_player_core, "plan_population_cells")
    expected_owner = {"gameplay/services/virtual_player_core/population_runtime.py"}
    assert _runtime_symbol_reference_paths("create_virtual_player_v2") == expected_owner
    assert _runtime_symbol_reference_paths("_issue_v2_bootstrap_population_permit") == expected_owner
    assert _runtime_symbol_reference_paths("_create_virtual_player_v1") == expected_owner
    assert "create_virtual_player_v2" not in bootstrap.__all__
    assert "_create_virtual_player_v1" not in bootstrap.__all__

    population_path = GAMEPLAY_ROOT / "services" / "virtual_player_core" / "population_runtime.py"
    population_tree = ast.parse(
        population_path.read_text(encoding="utf-8"),
        filename=str(population_path),
    )
    create_references = {
        node.name
        for node in population_tree.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(child, ast.Name) and child.id == "create_virtual_player_v2" for child in ast.walk(node))
    }
    permit_references = {
        node.name
        for node in population_tree.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.Name) and child.id == "_issue_v2_bootstrap_population_permit"
            for child in ast.walk(node)
        )
    }
    assert create_references == {"_reconcile_claimed_virtual_player_population_cell"}
    assert permit_references == {"_reactivate_or_create_virtual_player"}


def test_gate_evidence_binds_every_public_and_internal_bootstrap_owner() -> None:
    from gameplay.services.virtual_player_core import gate_evidence

    public_facade = "gameplay/services/virtual_players.py"
    bootstrap_owner = "gameplay/services/virtual_player_core/bootstrap.py"

    assert public_facade in gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES
    assert public_facade in gate_evidence.GATE_E_REQUIRED_SOURCE_FILES
    assert bootstrap_owner in gate_evidence.GATE_D1_REQUIRED_SOURCE_FILES
    assert bootstrap_owner in gate_evidence.GATE_E_REQUIRED_SOURCE_FILES


@pytest.mark.parametrize("relative_path", PURE_VIRTUAL_PLAYER_RULE_OWNERS)
def test_virtual_player_rule_owners_are_framework_independent(
    relative_path: str,
) -> None:
    path = PROJECT_ROOT / relative_path
    module_name = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(_resolve_import(module_name, node))

    assert not any(
        module == "django"
        or module.startswith("django.")
        or module == "gameplay.models"
        or module.startswith("gameplay.models.")
        for module in imported_modules
    )


def test_virtual_player_legacy_package_root_does_not_reexport_implementations() -> None:
    path = GAMEPLAY_ROOT / "services" / "virtual_player_core" / "legacy" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)


def test_virtual_reserve_facade_freezes_confirmed_production_entrypoints() -> None:
    from gameplay.services.arena import virtual_reserve

    assert set(virtual_reserve.__all__) == VIRTUAL_RESERVE_FACADE_EXPORTS
    assert len(virtual_reserve.__all__) == len(set(virtual_reserve.__all__)) == 11
    assert all(hasattr(virtual_reserve, name) for name in virtual_reserve.__all__)
    assert "ReserveReplenishmentResult" not in virtual_reserve.__all__
    assert "ArenaVirtualGrowthTarget" not in virtual_reserve.__all__
    _assert_thin_facade(GAMEPLAY_ROOT / "services" / "arena" / "virtual_reserve.py")
    _assert_framework_independent(GAMEPLAY_ROOT / "services" / "arena" / "virtual_lineups.py")


def test_virtual_reserve_production_consumers_only_import_public_names() -> None:
    consumers = _facade_imports("gameplay.services.arena.virtual_reserve")

    assert consumers == {}


def test_bot_profile_direct_import_inventory_is_frozen_with_a_purpose_for_every_reader() -> None:
    assert _direct_model_import_paths("BotProfile") == set(BOT_PROFILE_DIRECT_IMPORTS)
    assert all(purpose.strip() for purpose in BOT_PROFILE_DIRECT_IMPORTS.values())


def test_bot_profile_indirect_relation_reader_inventory_is_frozen() -> None:
    assert _bot_profile_relation_reader_paths() == set(BOT_PROFILE_RELATION_READERS)
    assert all(purpose.strip() for purpose in BOT_PROFILE_RELATION_READERS.values())
    for relative_path in READ_ONLY_VIRTUAL_PLAYER_OWNERS:
        _assert_model_read_only(PROJECT_ROOT / relative_path)


def test_gate_a_bot_profile_write_owner_ratchet_blocks_new_bypasses() -> None:
    assert _model_write_owners("BotProfile", relation_names=("bot_profile",)) == BOT_PROFILE_GATE_A_WRITE_OWNERS
    profile_store_path = GAMEPLAY_ROOT / "services" / "virtual_player_core" / "profile_store.py"
    profile_store_source = profile_store_path.read_text(encoding="utf-8")
    profile_store_tree = ast.parse(
        profile_store_source,
        filename=str(profile_store_path),
    )
    imported_models = _gameplay_model_imports(profile_store_path, profile_store_tree)
    assert imported_models == {"BotProfile", "Manor"}
    assert (
        find_model_dml(
            profile_store_source,
            model_name="Manor",
            filename=str(profile_store_path),
        )
        == ()
    )


def test_gate_activation_readiness_is_owned_by_evidence_workflows() -> None:
    ready_call_owners: dict[str, set[str]] = {
        "gate_d1_ready=True": set(),
        "gate_e_ready=True": set(),
    }
    for path in GAMEPLAY_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        for marker in ready_call_owners:
            if marker in source:
                ready_call_owners[marker].add(relative_path)

    assert ready_call_owners == {
        "gate_d1_ready=True": {"gameplay/services/virtual_player_core/gate_d1_exit_workflow.py"},
        "gate_e_ready=True": {"gameplay/services/virtual_player_core/gate_e_cutover_workflow.py"},
    }
    generic_command = (GAMEPLAY_ROOT / "management" / "commands" / "transition_virtual_player_routing.py").read_text(
        encoding="utf-8"
    )
    assert "--gate-d1-ready" not in generic_command
    assert "--gate-e-ready" not in generic_command


def test_prestige_domain_event_keeps_virtual_population_out_of_write_owners() -> None:
    prestige_path = GAMEPLAY_ROOT / "services" / "manor" / "prestige.py"
    prestige_source = prestige_path.read_text(encoding="utf-8")
    assert "virtual_player" not in prestige_source
    assert "prestige_change_committed.send" in prestige_source

    transition_sources = (
        prestige_source,
        (GAMEPLAY_ROOT / "services" / "raid" / "combat" / "battle.py").read_text(encoding="utf-8"),
        (GAMEPLAY_ROOT / "services" / "virtual_player_core" / "maintenance.py").read_text(encoding="utf-8"),
    )
    assert all("schedule_prestige_change_on_commit(" in source for source in transition_sources)

    signals_source = (GAMEPLAY_ROOT / "signals.py").read_text(encoding="utf-8")
    assert "merge_committed_prestige_transition_population_demands(" in signals_source
    assert "maintain_due_virtual_players" not in signals_source


def test_gate_a_arena_state_write_owner_ratchets_block_new_bypasses() -> None:
    assert _model_write_owners("ArenaVirtualDemand") == ARENA_DEMAND_GATE_A_WRITE_OWNERS
    assert (
        _model_write_owners(
            "ArenaVirtualReserveMember",
            relation_names=("reserve_members",),
        )
        == ARENA_MEMBER_GATE_A_WRITE_OWNERS
    )


@pytest.mark.parametrize(
    "source, expected_method",
    [
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.create(state='active')",
            "create",
        ),
        (
            "from gameplay.models import BotProfile as BP\nBP.objects.filter(id=1).update(state='stale')",
            "update",
        ),
        (
            "import gameplay.models as gm\ngm.BotProfile.objects.all().delete()",
            "delete",
        ),
        (
            "from gameplay.models import BotProfile\nprofiles = BotProfile.objects.filter(id=1)\nprofiles.update(state='stale')",
            "update",
        ),
        (
            "from gameplay.models import BotProfile\nprofile = BotProfile.objects.get(id=1)\nprofile.save()",
            "save",
        ),
        (
            "from gameplay.models import BotProfile\nprofile: BotProfile\nprofile.delete()",
            "delete",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.bulk_create([])",
            "bulk_create",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.bulk_update([], ['state'])",
            "bulk_update",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.get_or_create(id=1)",
            "get_or_create",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.update_or_create(id=1)",
            "update_or_create",
        ),
        (
            "import gameplay.models as gm\ngm.BotProfile.objects.acreate(state='active')",
            "acreate",
        ),
        (
            "import gameplay.models\ngameplay.models.BotProfile.objects.filter(id=1).aupdate(state='stale')",
            "aupdate",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.all().adelete()",
            "adelete",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.abulk_create([])",
            "abulk_create",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.abulk_update([], ['state'])",
            "abulk_update",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.aget_or_create(id=1)",
            "aget_or_create",
        ),
        (
            "from gameplay.models import BotProfile\nBotProfile.objects.aupdate_or_create(id=1)",
            "aupdate_or_create",
        ),
        (
            "from gameplay.models import BotProfile\n"
            "async def write():\n    profile = await BotProfile.objects.aget(id=1)\n    await profile.asave()",
            "asave",
        ),
        (
            "from gameplay.models import BotProfile\nprofile = BotProfile.objects.get(id=1)\nprofile.save_base()",
            "save_base",
        ),
        (
            "from gameplay.models import BotProfile\n"
            "def write_any(queryset):\n    queryset.update(state='stale')\n"
            "profiles = BotProfile.objects.filter(id=1)\nwrite_any(profiles)",
            "update",
        ),
        (
            "from django.contrib import admin\nfrom gameplay.models import BotProfile\n"
            "@admin.register(BotProfile)\nclass BotProfileAdmin(admin.ModelAdmin):\n"
            "    def mark(self, request, queryset):\n        queryset.update(state='stale')",
            "update",
        ),
    ],
)
def test_model_dml_gate_rejects_each_supported_bypass(source: str, expected_method: str) -> None:
    uses = find_model_dml(source, model_name="BotProfile")

    assert expected_method in {use.method for use in uses}


def test_model_dml_gate_allows_registered_read_only_patterns() -> None:
    source = """
from gameplay.models import BotProfile as BP

profile = BP.objects.filter(id=1).first()
exists = BP.objects.filter(state='active').exists()
states = list(BP.objects.order_by('id').values_list('state', flat=True))
"""

    assert find_model_dml(source, model_name="BotProfile") == ()


def test_model_import_gate_recognizes_module_aliases_and_dotted_imports() -> None:
    assert source_imports_model(
        "import gameplay.models as gm\ngm.BotProfile.objects.all()",
        model_name="BotProfile",
    )
    assert source_imports_model(
        "import gameplay.models\ngameplay.models.BotProfile.objects.all()",
        model_name="BotProfile",
    )


def test_model_dml_gate_rejects_reverse_relation_instance_writes() -> None:
    source = """
from gameplay.models import Manor

manor = Manor.objects.get(pk=1)
manor.bot_profile.save()
"""

    uses = find_model_dml(source, model_name="BotProfile", relation_names=("bot_profile",))

    assert {use.method for use in uses} == {"save"}


def test_arena_reverse_dependency_debt_is_explicit_and_cannot_grow() -> None:
    arena_root = GAMEPLAY_ROOT / "services" / "arena"
    arena_paths = sorted(arena_root.glob("*.py"))
    arena_modules = {_module_name(path) for path in arena_paths}
    reserve_modules = {
        "gameplay.services.arena.virtual_reserve_demand",
        "gameplay.services.arena.virtual_reserve_fill",
        "gameplay.services.arena.virtual_reserve_observability",
        "gameplay.services.arena.virtual_reserve_policy",
        "gameplay.services.arena.virtual_reserve_pool",
        "gameplay.services.arena.virtual_reserve_reconcile",
        "gameplay.services.arena.virtual_reserve_references",
        "gameplay.services.arena.virtual_reserve_scan",
    }
    lifecycle_modules = {
        "gameplay.services.arena.coop_core",
        "gameplay.services.arena.coop_lifecycle",
        "gameplay.services.arena.core",
    }
    arena_edges: set[tuple[str, str]] = set()
    reverse_edges: set[tuple[str, str]] = set()
    for path in arena_paths:
        source_module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target_modules = {_resolve_import(source_module, node)}
                target_modules.update(
                    f"{_resolve_import(source_module, node)}.{alias.name}"
                    for alias in node.names
                    if node.module is None
                )
            elif isinstance(node, ast.Import):
                target_modules = {alias.name for alias in node.names}
            else:
                continue
            for target_module in target_modules:
                edge = (source_module, target_module)
                if target_module in arena_modules:
                    arena_edges.add(edge)
                if source_module in reserve_modules and target_module in lifecycle_modules:
                    reverse_edges.add(edge)
                if (
                    source_module == "gameplay.services.arena.lifecycle_helpers"
                    and target_module
                    in reserve_modules
                    | {
                        "gameplay.services.arena.match_helpers",
                        "gameplay.services.arena.virtual_reserve",
                    }
                ):
                    reverse_edges.add(edge)

    reserve_edges = {edge for edge in arena_edges if edge[0] in reserve_modules and edge[1] in reserve_modules}
    assert reserve_edges == {
        (
            "gameplay.services.arena.virtual_reserve_demand",
            "gameplay.services.arena.virtual_reserve_references",
        ),
        (
            "gameplay.services.arena.virtual_reserve_fill",
            "gameplay.services.arena.virtual_reserve_observability",
        ),
        (
            "gameplay.services.arena.virtual_reserve_fill",
            "gameplay.services.arena.virtual_reserve_pool",
        ),
        (
            "gameplay.services.arena.virtual_reserve_fill",
            "gameplay.services.arena.virtual_reserve_reconcile",
        ),
        (
            "gameplay.services.arena.virtual_reserve_fill",
            "gameplay.services.arena.virtual_reserve_references",
        ),
        (
            "gameplay.services.arena.virtual_reserve_fill",
            "gameplay.services.arena.virtual_reserve_policy",
        ),
        (
            "gameplay.services.arena.virtual_reserve_pool",
            "gameplay.services.arena.virtual_reserve_observability",
        ),
        (
            "gameplay.services.arena.virtual_reserve_pool",
            "gameplay.services.arena.virtual_reserve_policy",
        ),
        (
            "gameplay.services.arena.virtual_reserve_pool",
            "gameplay.services.arena.virtual_reserve_references",
        ),
        (
            "gameplay.services.arena.virtual_reserve_demand",
            "gameplay.services.arena.virtual_reserve_policy",
        ),
        (
            "gameplay.services.arena.virtual_reserve_reconcile",
            "gameplay.services.arena.virtual_reserve_demand",
        ),
        (
            "gameplay.services.arena.virtual_reserve_reconcile",
            "gameplay.services.arena.virtual_reserve_observability",
        ),
        (
            "gameplay.services.arena.virtual_reserve_reconcile",
            "gameplay.services.arena.virtual_reserve_pool",
        ),
        (
            "gameplay.services.arena.virtual_reserve_scan",
            "gameplay.services.arena.virtual_reserve_fill",
        ),
        (
            "gameplay.services.arena.virtual_reserve_scan",
            "gameplay.services.arena.virtual_reserve_pool",
        ),
        (
            "gameplay.services.arena.virtual_reserve_scan",
            "gameplay.services.arena.virtual_reserve_reconcile",
        ),
    }
    _assert_acyclic_import_graph(arena_modules, arena_edges)
    assert reverse_edges == set()


def test_arena_task_reverse_dependency_debt_is_explicit() -> None:
    task_path = PROJECT_ROOT / "gameplay" / "tasks" / "arena.py"
    reserve_path = PROJECT_ROOT / "gameplay" / "services" / "arena" / "virtual_reserve.py"
    task_imports: dict[str, set[str]] = {}
    task_tree = ast.parse(task_path.read_text(encoding="utf-8"), filename=str(task_path))
    for node in ast.walk(task_tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        target_module = _resolve_import("gameplay.tasks.arena", node)
        if target_module.startswith("gameplay.services.arena.virtual_reserve"):
            task_imports.setdefault(target_module, set()).update(alias.name for alias in node.names)

    assert task_imports == {
        "gameplay.services.arena.virtual_reserve_pool": {
            "create_due_virtual_reserve_profiles",
            "grow_due_virtual_reserves",
            "replenish_virtual_reserve",
        },
        "gameplay.services.arena.virtual_reserve_reconcile": {
            "reconcile_coop_demand",
            "reconcile_tournament_demand",
        },
        "gameplay.services.arena.virtual_reserve_scan": {"scan_virtual_reserve_demands"},
        "gameplay.services.arena.virtual_reserve_observability": {
            "ARENA_SHORTAGE_METRIC_RETRY_MAX_ATTEMPTS",
            "is_retryable_arena_shortage_metric_error",
            "queue_arena_shortage_metric_retry",
            "record_arena_shortage_metric_failure",
            "record_arena_shortage_observation",
        },
    }

    reserve_tree = ast.parse(reserve_path.read_text(encoding="utf-8"), filename=str(reserve_path))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and _resolve_import("gameplay.services.arena.virtual_reserve", node) == "gameplay.tasks.arena"
        for node in ast.walk(reserve_tree)
    )
