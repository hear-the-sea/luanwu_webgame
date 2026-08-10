"""Read-only evidence and idempotent bootstrap for the single V2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.db import DatabaseError, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Count, F

from gameplay.models import (
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotPolicyRelease,
    BotProfile,
    BotRuntimeRoutingState,
    VirtualPlayerGrowthControlSnapshot,
)
from gameplay.services import runtime_configs

from .config import BootstrapMode, MaintenanceMode, VirtualPlayerConfigError, load_virtual_player_v2_config
from .policy_registry import release_configured_policy_operation

CheckSeverity = Literal["error", "warning"]
REQUIRED_RUNTIME_MIGRATIONS = (
    ("gameplay", "0167_growth_control_snapshot_immutability"),
    ("gameplay", "0168_virtualplayergrowthcontrolpointer_and_more"),
    ("gameplay", "0169_alter_botmaintenancerecovery_scope"),
    ("gameplay", "0170_arena_growth_target_driven_lifecycle"),
    ("gameplay", "0171_bot_maintenance_cycle_schedule"),
    ("gameplay", "0172_bot_maintenance_cycle_interval_seed"),
    ("gameplay", "0173_remove_legacy_arena_lifecycle_fields"),
    ("gameplay", "0174_bot_maintenance_completion_event"),
    ("gameplay", "0175_botmaintenanceattempt_action_kind_and_more"),
    ("gameplay", "0176_virtual_player_recruitment_due_and_cycle_budget"),
    ("gameplay", "0177_virtual_player_attempt_trigger_dimensions_index"),
    ("guests", "0071_guestrecruitment_virtual_source"),
)


@dataclass(frozen=True, slots=True)
class RuntimePreflightCheck:
    code: str
    passed: bool
    detail: str
    severity: CheckSeverity = "error"


@dataclass(frozen=True, slots=True)
class RuntimePreflightReport:
    checks: tuple[RuntimePreflightCheck, ...]

    @property
    def errors(self) -> tuple[RuntimePreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.passed and check.severity == "error")

    @property
    def warnings(self) -> tuple[RuntimePreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.passed and check.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


def _check(
    code: str,
    passed: bool,
    detail: str,
    *,
    severity: CheckSeverity = "error",
) -> RuntimePreflightCheck:
    return RuntimePreflightCheck(
        code=code,
        passed=bool(passed),
        detail=str(detail),
        severity=severity,
    )


def _config_checks() -> list[RuntimePreflightCheck]:
    try:
        config = load_virtual_player_v2_config()
    except (VirtualPlayerConfigError, ValueError) as exc:
        return [_check("config_load", False, f"{type(exc).__name__}: {exc}")]
    if config is None:
        return [_check("config_load", False, "bot_development_v2 is not configured")]

    checks = [
        _check(
            "config_single_policy",
            tuple(sorted(config.policies)) == (2,),
            f"configured policy versions={tuple(sorted(config.policies))!r}",
        ),
        _check(
            "config_policy_rollout",
            config.policy_rollout.target_version == 2
            and not config.policy_rollout.enabled
            and config.policy_rollout.rollout_percent == 0,
            (
                "target=%s enabled=%s percent=%s"
                % (
                    config.policy_rollout.target_version,
                    config.policy_rollout.enabled,
                    config.policy_rollout.rollout_percent,
                )
            ),
        ),
        _check(
            "config_v2_active",
            config.routing.bootstrap_mode is BootstrapMode.V2_ACTIVE
            and config.routing.maintenance_mode is MaintenanceMode.V2_ACTIVE,
            f"bootstrap={config.routing.bootstrap_mode.value} maintenance={config.routing.maintenance_mode.value}",
        ),
        _check(
            "config_static_reference_retired",
            not config.reference_snapshot_catalog,
            f"reference_snapshot_catalog entries={len(config.reference_snapshot_catalog)}",
        ),
    ]
    try:
        policy = config.policy(2)
    except VirtualPlayerConfigError as exc:
        checks.append(_check("config_policy_2", False, str(exc)))
    else:
        checks.append(_check("config_policy_2", policy.version == 2 and bool(policy.checksum), policy.checksum))
    return checks


def _database_checks() -> list[RuntimePreflightCheck]:
    checks: list[RuntimePreflightCheck] = []
    try:
        releases = tuple(BotPolicyRelease.objects.order_by("version"))
        current_releases = tuple(release for release in releases if int(release.version) == 2)
        legacy_releases = tuple(release for release in releases if int(release.version) != 2)
        checks.append(
            _check(
                "db_policy_2_release",
                len(current_releases) == 1 and current_releases[0].retired_at is None,
                f"active policy-2 releases={len(current_releases)}",
            )
        )
        checks.append(
            _check(
                "db_legacy_policy_releases",
                not legacy_releases,
                f"legacy policy release rows={len(legacy_releases)}",
            )
        )

        legacy_profiles = BotProfile.objects.exclude(engine_version=2, policy_version=2)
        checks.append(
            _check(
                "db_single_policy_profiles",
                not legacy_profiles.exists(),
                f"legacy or non-policy-2 profile rows={legacy_profiles.count()}",
            )
        )

        state = BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
        if state is None:
            checks.append(_check("db_routing_initialized", False, "singleton routing row is absent"))
        else:
            try:
                routes = runtime_configs.parse_calibration_routes(state.calibration_routes)
            except runtime_configs.RuntimeRoutingError as exc:
                checks.append(_check("db_static_calibration_routes", False, f"invalid persisted route: {exc}"))
            else:
                checks.append(
                    _check(
                        "db_static_calibration_routes",
                        not routes,
                        f"persisted calibration route rows={len(routes)}",
                    )
                )
            checks.extend(
                (
                    _check(
                        "db_v2_active_routing",
                        state.bootstrap_mode == BootstrapMode.V2_ACTIVE.value
                        and state.maintenance_mode == MaintenanceMode.V2_ACTIVE.value,
                        f"bootstrap={state.bootstrap_mode} maintenance={state.maintenance_mode}",
                    ),
                    _check(
                        "db_policy_rollout_disabled",
                        state.policy_rollout_target_version == 2
                        and not state.policy_rollout_enabled
                        and state.policy_rollout_percent == 0,
                        (
                            "target=%s enabled=%s percent=%s"
                            % (
                                state.policy_rollout_target_version,
                                state.policy_rollout_enabled,
                                state.policy_rollout_percent,
                            )
                        ),
                    ),
                )
            )

        old_claims = ArenaVirtualReserveMember.objects.filter(growth_request_digest_schema=1).count()
        checks.append(
            _check(
                "db_legacy_arena_claims",
                old_claims == 0,
                f"schema-1 arena member rows={old_claims}",
            )
        )
        undercounted_admission_rows = (
            ArenaVirtualDemand.objects.annotate(reserve_member_count=Count("reserve_members"))
            .filter(admission_attempt_high_water__lt=F("reserve_member_count"))
            .count()
        )
        checks.append(
            _check(
                "db_admission_counter_consistency",
                undercounted_admission_rows == 0,
                f"admission high-water undercounts={undercounted_admission_rows}",
            )
        )
        invalid_control_rows = VirtualPlayerGrowthControlSnapshot.objects.exclude(policy_version=2).count()
        checks.append(
            _check(
                "db_growth_control_policy_2",
                invalid_control_rows == 0,
                f"non-policy-2 growth control rows={invalid_control_rows}",
            )
        )

        applied = set(
            MigrationRecorder.Migration.objects.filter(
                app__in={app for app, _name in REQUIRED_RUNTIME_MIGRATIONS}
            ).values_list("app", "name")
        )
        missing_migrations = tuple(
            f"{app}.{name}" for app, name in REQUIRED_RUNTIME_MIGRATIONS if (app, name) not in applied
        )
        checks.append(
            _check(
                "db_virtual_player_migrations",
                not missing_migrations,
                (
                    "all required virtual-player migrations are applied"
                    if not missing_migrations
                    else f"missing virtual-player migrations={missing_migrations}"
                ),
            )
        )
    except DatabaseError as exc:
        checks.append(_check("db_read", False, f"database read failed: {type(exc).__name__}: {exc}"))
    return checks


def inspect_virtual_player_v2_runtime() -> RuntimePreflightReport:
    """Collect immutable config and persisted-state evidence without writes."""

    return RuntimePreflightReport(checks=tuple([*_config_checks(), *_database_checks()]))


@transaction.atomic
def initialize_virtual_player_v2_runtime(*, apply: bool = False) -> RuntimePreflightReport:
    """Publish policy 2 and converge routing through the existing CAS service.

    ``apply=False`` is a dry-run.  The function intentionally does not run
    migrations or delete legacy rows; those are deployment/database actions
    requiring a separately confirmed maintenance window.
    """

    config = load_virtual_player_v2_config()
    if config is None:
        raise VirtualPlayerConfigError("bot_development_v2 is not configured")
    applied_migrations = set(
        MigrationRecorder.Migration.objects.filter(
            app__in={app for app, _name in REQUIRED_RUNTIME_MIGRATIONS}
        ).values_list("app", "name")
    )
    missing_migrations = tuple(
        f"{app}.{name}" for app, name in REQUIRED_RUNTIME_MIGRATIONS if (app, name) not in applied_migrations
    )
    if missing_migrations:
        raise RuntimeError(
            f"virtual-player migrations {missing_migrations} must be applied before V2 runtime initialization"
        )
    release_configured_policy_operation(version=2, apply=apply)
    state = BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None:
        expected_revision = None
        expected_bootstrap = None
        expected_maintenance = None
    else:
        expected_revision = int(state.revision)
        expected_bootstrap = state.bootstrap_mode
        expected_maintenance = state.maintenance_mode
    runtime_configs.transition_virtual_player_routing_operation(
        expected_revision=expected_revision,
        expected_bootstrap_mode=expected_bootstrap,
        expected_maintenance_mode=expected_maintenance,
        bootstrap_mode=BootstrapMode.V2_ACTIVE,
        maintenance_mode=MaintenanceMode.V2_ACTIVE,
        calibration_routes=(),
        apply=apply,
    )
    return inspect_virtual_player_v2_runtime()


__all__ = [
    "RuntimePreflightCheck",
    "RuntimePreflightReport",
    "initialize_virtual_player_v2_runtime",
    "inspect_virtual_player_v2_runtime",
]
