from __future__ import annotations

import base64
import json
import math
import random
import re
import threading
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, reset_queries
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import BotMaintenanceExecution, BotProfile, BotRuntimeRoutingState, Manor
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.config import load_virtual_player_v2_config
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    MaintenanceOutcome,
    MaintenanceTrigger,
)
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.projection import (
    ReferenceCandidate,
    ReferenceSelection,
    ReferenceSource,
    SampleTier,
    StrengthSummary,
)
from gameplay.services.virtual_player_core.random_context import RandomContext
from gameplay.services.virtual_player_core.safety_metrics import record_safety_heartbeat
from gameplay.services.virtual_player_core.stage_metrics import (
    GATE_E_OPTIONAL_STAGE_NAMES,
    GATE_E_REQUIRED_STAGE_NAMES,
    MaintenanceStageObservation,
    capture_maintenance_stage_metrics,
    current_maintenance_stage_metrics,
)
from gameplay.services.virtual_player_core.strategy import development_plan_catalog_v1, generate_development_plan
from guests.models import Guest, GuestTemplate
from guests.services.recruitment_guests import create_guest_from_template

pytestmark = [pytest.mark.integration]

_WARMUP_RUNS = 5
_MEASURED_RUNS = 30
_WRITE_PREFIXES = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
_STAGE_NAMES = GATE_E_REQUIRED_STAGE_NAMES
_OPTIONAL_STAGE_NAMES = GATE_E_OPTIONAL_STAGE_NAMES


@dataclass(frozen=True)
class _WorkerSample:
    maintained: int
    query_count: int
    write_query_count: int
    row_lock_wait_ms: float
    action_kinds: tuple[str, ...]
    query_summary: tuple[tuple[str, int], ...]
    stage_observations: tuple[MaintenanceStageObservation, ...] = ()


def _require_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("Gate E Maintenance evidence requires MySQL row locks")


def _nearest_rank(values: list[float], quantile: float) -> float:
    assert values
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


def _row_lock_time_ms() -> float:
    with connection.cursor() as cursor:
        cursor.execute("SHOW SESSION STATUS LIKE 'Innodb_row_lock_time'")
        row = cursor.fetchone()
    if row is None:
        raise AssertionError("MySQL did not expose Innodb_row_lock_time")
    return float(row[1])


def _write_query_count(captured_queries: list[dict[str, str]]) -> int:
    return sum(query["sql"].lstrip().upper().startswith(_WRITE_PREFIXES) for query in captured_queries)


def _query_summary(
    captured_queries: list[dict[str, str]],
) -> tuple[tuple[str, int], ...]:
    fingerprints: Counter[str] = Counter()
    for query in captured_queries:
        sql = " ".join(query["sql"].split())
        sql = re.sub(r"'(?:''|[^'])*'", "'?'", sql)
        sql = re.sub(r"\b\d+\b", "?", sql)
        fingerprints[sql[:240]] += 1
    return tuple(fingerprints.most_common(40))


def _action_kinds(captured_queries: list[dict[str, str]]) -> tuple[str, ...]:
    sql = "\n".join(query["sql"].upper() for query in captured_queries)
    markers = (
        ("building_upgrade", "UPDATE `GAMEPLAY_BUILDING` SET"),
        ("equipment_equip", "UPDATE `GUESTS_GEARITEM` SET"),
        ("guest_healing", "INSERT INTO `GUESTS_GUESTHEALTHLOG`"),
        ("inventory_acquisition", "BOTINVENTORYDAILYCOUNTER"),
        ("skill_learning", "INSERT INTO `GUESTS_GUESTSKILL`"),
        ("technology_upgrade", "GAMEPLAY_PLAYERTECHNOLOGY` SET"),
        ("training", "INSERT INTO `GUESTS_TRAININGLOG`"),
        ("troop_recruitment", "INSERT INTO `GAMEPLAY_TROOPRECRUITMENT`"),
    )
    return tuple(action for action, marker in markers if marker in sql)


def _stage_query_execute_wrapper(execute, sql, params, many, context):
    metrics = current_maintenance_stage_metrics()
    if metrics is not None:
        metrics.record_query(sql)
    return execute(sql, params, many, context)


def _stage_fingerprint_token(fingerprints: tuple[tuple[str, int], ...]) -> str:
    payload = [{"sql": sql, "count": count} for sql, count in fingerprints]
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=") or "-"


def _print_stage_metrics(samples: list[_WorkerSample], *, batch_size: int, concurrency: int) -> None:
    all_stage_names = (*_STAGE_NAMES, *_OPTIONAL_STAGE_NAMES)
    observations_by_stage: dict[str, list[MaintenanceStageObservation]] = {stage: [] for stage in all_stage_names}
    fingerprints_by_stage: dict[str, Counter[str]] = {stage: Counter() for stage in all_stage_names}
    for sample in samples:
        for observation in sample.stage_observations:
            if observation.stage not in observations_by_stage:
                raise AssertionError(f"unexpected maintenance stage: {observation.stage}")
            observations_by_stage[observation.stage].append(observation)
            fingerprints_by_stage[observation.stage].update(dict(observation.query_fingerprints))

    for stage in all_stage_names:
        observations = observations_by_stage[stage]
        if not observations:
            if stage in _STAGE_NAMES:
                raise AssertionError(f"no observations recorded for maintenance stage {stage}")
            continue
        durations = [observation.duration_ms for observation in observations]
        fingerprints = tuple(fingerprints_by_stage[stage].most_common(10))
        print(
            "gate_e_maintenance_stage "
            f"batch_size={batch_size} concurrency={concurrency} stage={stage} "
            f"observations={len(observations)} "
            f"duration_p50_ms={_nearest_rank(durations, 0.50):.3f} "
            f"duration_p95_ms={_nearest_rank(durations, 0.95):.3f} "
            f"duration_p99_ms={_nearest_rank(durations, 0.99):.3f} "
            f"queries_max={max(observation.query_count for observation in observations)} "
            f"write_queries_max={max(observation.write_query_count for observation in observations)} "
            f"fingerprints_b64={_stage_fingerprint_token(fingerprints)}"
        )


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


def _activate_v2_maintenance() -> None:
    BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        calibration_routes=[],
        revision=1,
    )


def _install_permissive_reference(monkeypatch) -> None:
    cap = StrengthSummary(
        composite=1_000_000_000,
        components={
            "arena_lineup_power": 1_000_000_000,
            "core_building_level": 1_000_000_000,
            "guest_count": 1_000_000_000,
            "max_guest_level": 1_000_000_000,
            "prestige": 1_000_000_000,
            "troop_total": 1_000_000_000,
        },
    )

    def _select_reference(*, prestige_band, **_kwargs):
        anchor = ReferenceCandidate(
            business_key=f"gate-e-benchmark:{prestige_band}",
            prestige_band=prestige_band,
            strength=cap,
            features={
                "core_building_level": 100,
                "guest_count": 100,
                "max_guest_level": 100,
            },
        )
        selection = ReferenceSelection(
            prestige_band=prestige_band,
            tier=SampleTier.SPARSE,
            source=ReferenceSource.LOCAL,
            local_sample_count=1,
            anchor=anchor,
            cap=cap,
            nearest_candidate_keys=(anchor.business_key,),
        )
        return 2, selection, "a" * 64

    monkeypatch.setattr(maintenance, "growth_control_reference_selection", _select_reference)
    # The deterministic benchmark double replaces the production resolver;
    # keep its planning snapshot preload side-effect free as well.
    monkeypatch.setattr(maintenance, "effective_growth_control_snapshots", lambda **_kwargs: {})


def _create_v2_profiles(*, count: int, now, policy) -> list[BotProfile]:
    config = load_virtual_player_v2_config()
    assert config is not None
    guest_template = GuestTemplate.objects.order_by("key").first()
    assert guest_template is not None
    profiles: list[BotProfile] = []
    for index in range(count):
        seed = 995_001 + index
        username = f"gate_e_maintenance_{seed}"
        user_model = get_user_model()
        user_model.objects.bulk_create([user_model(username=username, is_active=False)])
        user = user_model.objects.get(username=username)
        manor = Manor.objects.create(
            user=user,
            name=f"gate-e-{seed}"[-20:],
            region="north",
            prestige=400,
            silver=100_000,
            grain=100_000,
            silver_capacity=100_000,
            grain_capacity=100_000,
            resource_updated_at=now,
            last_active_at=now,
        )
        context = RandomContext(
            rng_version=config.rng_version,
            growth_seed=seed,
            engine_version=config.engine_version,
            plan_schema_version=config.plan_schema_version,
            policy_version=policy.version,
            maintenance_sequence=0,
        )
        development_plan = generate_development_plan(
            context=context,
            archetype=BotProfile.Archetype.BALANCED,
            catalog=development_plan_catalog_v1(),
        )
        profile = BotProfile.objects.create(
            manor=manor,
            archetype=BotProfile.Archetype.BALANCED,
            state=BotProfile.State.ACTIVE,
            prestige_band="newbie",
            target_prestige_band="newbie",
            current_prestige_band="newbie",
            growth_seed=seed,
            growth_stage=1,
            next_growth_at=now,
            abandon_at=now + timedelta(days=365),
            retire_at=now + timedelta(days=730),
            engine_version=config.engine_version,
            rng_version=config.rng_version,
            plan_schema_version=config.plan_schema_version,
            policy_version=policy.version,
            policy_checksum=policy.checksum,
            development_profile=development_plan.to_payload(),
            last_strength_increase_at=now - timedelta(days=1),
            v2_enrolled_at=now - timedelta(days=1),
            maintenance_started_at=now - timedelta(days=1),
            last_planned_at=now,
        )
        create_guest_from_template(
            manor=manor,
            template=guest_template,
            rng=random.Random(seed),
            grant_skills=False,
        )
        profiles.append(profile)

    profile_ids = [int(profile.id) for profile in profiles]
    manor_ids = [int(profile.manor_id) for profile in profiles]
    BotProfile.objects.filter(pk__in=profile_ids).update(
        next_growth_at=now,
        last_strength_increase_at=now - timedelta(days=1),
        strength_budget_entries=[],
        maintenance_sequence=0,
    )
    Manor.objects.filter(pk__in=manor_ids).update(
        silver=100_000,
        grain=100_000,
        silver_capacity=100_000,
        grain_capacity=100_000,
        resource_updated_at=now,
    )
    return list(BotProfile.objects.filter(pk__in=profile_ids).order_by("id"))


def _prepare_benchmark_cycle(profiles: list[BotProfile], *, now) -> None:
    profile_ids = [int(profile.id) for profile in profiles]
    manor_ids = [int(profile.manor_id) for profile in profiles]
    BotProfile.objects.filter(pk__in=profile_ids).update(next_growth_at=now)
    Manor.objects.filter(pk__in=manor_ids).update(
        silver=100_000,
        grain=100_000,
        resource_updated_at=now,
    )
    record_safety_heartbeat("safety_monitor", now=now)


def _run_worker(
    *,
    batch_size: int,
    now,
    start: threading.Barrier | None,
    collect_stage_metrics: bool = False,
) -> _WorkerSample:
    close_old_connections()
    try:
        if start is not None:
            start.wait(timeout=10)
        lock_wait_before = _row_lock_time_ms()
        reset_queries()
        with ExitStack() as stack:
            stage_metrics = None
            if collect_stage_metrics:
                stage_metrics = stack.enter_context(capture_maintenance_stage_metrics())
                stack.enter_context(connection.execute_wrapper(_stage_query_execute_wrapper))
            captured = stack.enter_context(CaptureQueriesContext(connection))
            maintained = maintenance.maintain_due_virtual_players(
                now=now,
                limit=batch_size,
            )
        lock_wait_after = _row_lock_time_ms()
        queries = list(captured.captured_queries)
        stage_observations = ()
        if stage_metrics is not None:
            stage_observations = tuple(
                observation for observations in stage_metrics.observations.values() for observation in observations
            )
        return _WorkerSample(
            maintained=maintained,
            query_count=len(queries),
            write_query_count=_write_query_count(queries),
            row_lock_wait_ms=max(0.0, lock_wait_after - lock_wait_before),
            action_kinds=_action_kinds(queries),
            query_summary=_query_summary(queries),
            stage_observations=stage_observations,
        )
    finally:
        close_old_connections()


def _run_batch(
    *,
    batch_size: int,
    concurrency: int,
    now,
    collect_stage_metrics: bool = False,
) -> tuple[float, list[_WorkerSample]]:
    if concurrency == 1:
        started = perf_counter()
        sample = _run_worker(
            batch_size=batch_size,
            now=now,
            start=None,
            collect_stage_metrics=collect_stage_metrics,
        )
        return (perf_counter() - started) * 1_000, [sample]

    start = threading.Barrier(concurrency)
    samples: list[_WorkerSample] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def _target() -> None:
        try:
            sample = _run_worker(
                batch_size=batch_size,
                now=now,
                start=start,
                collect_stage_metrics=collect_stage_metrics,
            )
            with guard:
                samples.append(sample)
        except BaseException as exc:  # pragma: no cover - asserted below
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=_target, daemon=True) for _index in range(concurrency)]
    started = perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    duration_ms = (perf_counter() - started) * 1_000

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(samples) == concurrency
    return duration_ms, samples


@pytest.mark.django_db(transaction=True)
def test_mysql_scheduled_planning_snapshot_executes_one_profile(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)
    now = timezone.now()
    profile = _create_v2_profiles(
        count=1,
        now=now,
        policy=released_v2_policy,
    )[0]
    record_safety_heartbeat("safety_monitor", now=timezone.now())
    preflight = maintenance.check_v2_development_write_preflight()
    assert preflight.allowed, preflight
    loaded_profile = BotProfile.objects.filter(pk=profile.pk).select_related("manor").get()
    snapshot = maintenance._scheduled_planning_snapshots(
        (loaded_profile,),
        planned_at=now,
    )[int(profile.id)]

    result = maintenance.maintain_virtual_player_v2(
        int(profile.id),
        trigger=MaintenanceTrigger.SCHEDULED,
        now=now,
        _planning_snapshot=snapshot,
    )

    assert result.outcome is MaintenanceOutcome.APPLIED


def test_scheduled_v2_batch_lock_busy_does_not_enter_owner(monkeypatch) -> None:
    owner_calls: list[dict[str, object]] = []
    release_calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        maintenance,
        "read_virtual_player_routing",
        lambda: SimpleNamespace(
            maintenance_mode=maintenance.MaintenanceMode.V2_ACTIVE,
        ),
    )
    monkeypatch.setattr(
        maintenance,
        "acquire_action_lock",
        lambda *_args, **_kwargs: (False, "scheduled-lock", None),
    )
    monkeypatch.setattr(
        maintenance,
        "_maintain_due_virtual_players_v2",
        lambda **kwargs: owner_calls.append(kwargs),
    )
    monkeypatch.setattr(
        maintenance,
        "release_action_lock",
        lambda lock_key, *, lock_token, **_kwargs: release_calls.append((lock_key, lock_token)),
    )

    assert maintenance.maintain_due_virtual_players(now=timezone.now(), limit=100) == 0
    assert owner_calls == []
    assert release_calls == []


def test_scheduled_v2_batch_lock_releases_owner_token_on_owner_error(
    monkeypatch,
) -> None:
    events: list[object] = []

    def _raise_from_owner(**_kwargs) -> int:
        events.append("owner")
        raise RuntimeError("injected V2 batch owner failure")

    def _release(lock_key, *, lock_token, **_kwargs) -> None:
        events.append(("release", lock_key, lock_token))

    monkeypatch.setattr(
        maintenance,
        "read_virtual_player_routing",
        lambda: SimpleNamespace(
            maintenance_mode=maintenance.MaintenanceMode.V2_ACTIVE,
        ),
    )
    monkeypatch.setattr(
        maintenance,
        "acquire_action_lock",
        lambda *_args, **_kwargs: (True, "scheduled-lock", "owner-token"),
    )
    monkeypatch.setattr(
        maintenance,
        "_maintain_due_virtual_players_v2",
        _raise_from_owner,
    )
    monkeypatch.setattr(maintenance, "release_action_lock", _release)

    with pytest.raises(RuntimeError, match="injected V2 batch owner failure"):
        maintenance.maintain_due_virtual_players(now=timezone.now(), limit=100)

    assert events == ["owner", ("release", "scheduled-lock", "owner-token")]


@pytest.mark.django_db(transaction=True)
def test_same_profile_double_worker_has_one_committed_winner(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)
    now = timezone.now()
    profile = _create_v2_profiles(
        count=1,
        now=now,
        policy=released_v2_policy,
    )[0]
    record_safety_heartbeat("safety_monitor", now=timezone.now())

    original_lock = maintenance.profile_store.lock_maintained_profile_with_scheduled_cycle
    contenders_ready = threading.Barrier(2)

    def _synchronized_lock(*args, **kwargs):
        contenders_ready.wait(timeout=10)
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(
        maintenance.profile_store,
        "lock_maintained_profile_with_scheduled_cycle",
        _synchronized_lock,
    )
    start = threading.Barrier(2)
    results = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def _worker(ordinal: int) -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = maintenance.maintain_virtual_player_v2(
                int(profile.id),
                trigger=MaintenanceTrigger.SCHEDULED,
                operation_id=uuid4(),
                attempt_ordinal=ordinal,
                now=now,
            )
            with guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(index + 1,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    profile.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(result.outcome.value for result in results) == ["applied", "busy"]
    assert profile.maintenance_sequence == 1
    applied_results = [result for result in results if result.outcome is MaintenanceOutcome.APPLIED]
    assert len(applied_results) == 1
    assert applied_results[0].action_kind


@pytest.mark.django_db(transaction=True)
def test_same_arena_operation_double_worker_commits_one_execution_receipt(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)
    now = timezone.now()
    profile = _create_v2_profiles(
        count=1,
        now=now,
        policy=released_v2_policy,
    )[0]
    record_safety_heartbeat("safety_monitor", now=timezone.now())

    original_lock = maintenance.profile_store.lock_maintained_profile
    contenders_ready = threading.Barrier(2)

    def _synchronized_lock(*args, **kwargs):
        contenders_ready.wait(timeout=10)
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(
        maintenance.profile_store,
        "lock_maintained_profile",
        _synchronized_lock,
    )
    operation_id = f"arena-growth-concurrent-{uuid4().hex}"
    start = threading.Barrier(2)
    results: list[AcceleratedGrowthOutcome] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def _worker(ordinal: int) -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = maintenance.accelerate_virtual_player_growth(
                int(profile.id),
                operation_id=operation_id,
                attempt_ordinal=ordinal,
                now=now,
                minimum_guest_count=1,
                minimum_guest_level=2,
                guest_rarity_cap="purple",
                max_guest_level_step=20,
            )
            with guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(index + 1,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    profile.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    receipt = BotMaintenanceExecution.objects.get(operation_id=operation_id)
    committed_outcome = {
        BotMaintenanceExecution.Outcome.APPLIED: AcceleratedGrowthOutcome.GROWN,
        BotMaintenanceExecution.Outcome.NO_ACTION: AcceleratedGrowthOutcome.NO_ACTION,
    }[receipt.outcome]
    assert committed_outcome in results
    assert set(results) <= {committed_outcome, AcceleratedGrowthOutcome.BUSY}
    assert profile.maintenance_sequence == 1
    assert BotMaintenanceExecution.objects.filter(operation_id=operation_id).count() == 1
    assert (
        Guest.objects.filter(
            manor_id=profile.manor_id,
            training_complete_at__isnull=False,
        ).count()
        == 1
    )

    replay = maintenance.accelerate_virtual_player_growth(
        int(profile.id),
        operation_id=operation_id,
        attempt_ordinal=3,
        now=now,
        minimum_guest_count=1,
        minimum_guest_level=2,
        guest_rarity_cap="purple",
        max_guest_level_step=20,
    )
    profile.refresh_from_db()
    assert replay is committed_outcome
    assert profile.maintenance_sequence == 1
    assert BotMaintenanceExecution.objects.filter(operation_id=operation_id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_mysql_failure_boundaries_do_not_duplicate_or_partially_commit(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)
    for candidate_builder in (
        "build_equipment_equip_candidates",
        "_troop_recruitment_candidates",
        "build_skill_learning_candidates",
        "build_inventory_acquisition_candidates",
    ):
        monkeypatch.setattr(maintenance, candidate_builder, lambda **_kwargs: ((), {}))
    monkeypatch.setattr(maintenance, "_building_upgrade_quotes", lambda **_kwargs: ())
    monkeypatch.setattr(maintenance, "_technology_upgrade_quotes", lambda **_kwargs: ())
    now = timezone.now()
    profile = _create_v2_profiles(
        count=1,
        now=now,
        policy=released_v2_policy,
    )[0]
    record_safety_heartbeat("safety_monitor", now=timezone.now())

    original_execute = maintenance.execute_virtual_player_v2_maintenance_plan

    def _fail_before_business_write(_plan, **_kwargs):
        raise RuntimeError("injected before business write")

    monkeypatch.setattr(
        maintenance,
        "execute_virtual_player_v2_maintenance_plan",
        _fail_before_business_write,
    )
    with pytest.raises(RuntimeError, match="injected before business write"):
        maintenance.maintain_virtual_player_v2(
            int(profile.id),
            trigger=MaintenanceTrigger.SCHEDULED,
            operation_id=uuid4(),
            now=now,
        )
    profile.refresh_from_db()
    assert profile.maintenance_sequence == 0
    assert not Guest.objects.filter(
        manor_id=profile.manor_id,
        training_complete_at__isnull=False,
    ).exists()

    monkeypatch.setattr(
        maintenance,
        "execute_virtual_player_v2_maintenance_plan",
        original_execute,
    )
    plan = maintenance.build_virtual_player_v2_maintenance_plan(
        int(profile.id),
        trigger=MaintenanceTrigger.SCHEDULED,
        now=now,
    )
    assert plan.action_kind == "training"
    import guests.services.training as training_service

    original_ensure_auto_training = training_service.ensure_auto_training

    def _fail_after_domain_write(guest, **kwargs):
        original_ensure_auto_training(guest, **kwargs)
        raise RuntimeError("injected after domain write")

    monkeypatch.setattr(training_service, "ensure_auto_training", _fail_after_domain_write)
    with pytest.raises(RuntimeError, match="injected after domain write"):
        maintenance.execute_virtual_player_v2_maintenance_plan(plan)
    profile.refresh_from_db()
    assert profile.maintenance_sequence == 0
    assert not Guest.objects.filter(
        manor_id=profile.manor_id,
        training_complete_at__isnull=False,
    ).exists()

    monkeypatch.setattr(training_service, "ensure_auto_training", original_ensure_auto_training)
    original_finish = maintenance._finish_safety_attempt_best_effort

    def _fail_after_business_commit(*_args, **_kwargs):
        raise RuntimeError("injected after business commit")

    monkeypatch.setattr(
        maintenance,
        "_finish_safety_attempt_best_effort",
        _fail_after_business_commit,
    )
    operation_id = uuid4()
    with pytest.raises(RuntimeError, match="injected after business commit"):
        maintenance.maintain_virtual_player_v2(
            int(profile.id),
            trigger=MaintenanceTrigger.SCHEDULED,
            operation_id=operation_id,
            now=now,
        )
    profile.refresh_from_db()
    assert profile.maintenance_sequence == 1
    assert (
        Guest.objects.filter(
            manor_id=profile.manor_id,
            training_complete_at__isnull=False,
        ).count()
        == 1
    )

    monkeypatch.setattr(
        maintenance,
        "_finish_safety_attempt_best_effort",
        original_finish,
    )
    record_safety_heartbeat("safety_monitor", now=timezone.now())
    retry = maintenance.maintain_virtual_player_v2(
        int(profile.id),
        trigger=MaintenanceTrigger.SCHEDULED,
        operation_id=operation_id,
        attempt_ordinal=2,
        now=now,
    )
    profile.refresh_from_db()
    # Ordinary V2 maintenance does not create an Arena execution receipt.
    # Once the committed training is visible, the next scheduled plan has no
    # eligible candidate and therefore reports a committed NO_ACTION result.
    assert retry.outcome is MaintenanceOutcome.NO_ACTION
    assert retry.reason == "no_eligible_candidate"
    assert profile.maintenance_sequence == 2
    assert (
        Guest.objects.filter(
            manor_id=profile.manor_id,
            training_complete_at__isnull=False,
        ).count()
        == 1
    )


@pytest.mark.parametrize("batch_size", (1, 10, 100))
@pytest.mark.parametrize("concurrency", (1, 2))
@pytest.mark.django_db(transaction=True)
def test_v2_maintenance_meets_frozen_mysql_benchmark_matrix(
    batch_size: int,
    concurrency: int,
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)
    base_now = timezone.now()
    profiles = _create_v2_profiles(
        count=batch_size,
        now=base_now,
        policy=released_v2_policy,
    )

    durations_ms: list[float] = []
    query_counts: list[int] = []
    write_query_counts: list[int] = []
    lock_waits_ms: list[float] = []
    measured_samples: list[_WorkerSample] = []

    for index in range(_WARMUP_RUNS + _MEASURED_RUNS):
        cycle_now = base_now + timedelta(days=index + 1)
        _prepare_benchmark_cycle(profiles, now=cycle_now)
        duration_ms, worker_samples = _run_batch(
            batch_size=batch_size,
            concurrency=concurrency,
            now=cycle_now,
            collect_stage_metrics=index >= _WARMUP_RUNS,
        )
        assert sum(sample.maintained for sample in worker_samples) == batch_size
        assert all(sample.query_count > 0 for sample in worker_samples)
        if index >= _WARMUP_RUNS:
            durations_ms.append(duration_ms)
            query_counts.append(max(sample.query_count for sample in worker_samples))
            write_query_counts.append(max(sample.write_query_count for sample in worker_samples))
            lock_waits_ms.extend(sample.row_lock_wait_ms for sample in worker_samples)
            measured_samples.extend(worker_samples)

    duration_p95_ms = _nearest_rank(durations_ms, 0.95)
    duration_p99_ms = _nearest_rank(durations_ms, 0.99)
    lock_wait_p95_ms = _nearest_rank(lock_waits_ms, 0.95)
    lock_wait_p99_ms = _nearest_rank(lock_waits_ms, 0.99)
    print(
        "gate_e_maintenance_benchmark "
        f"batch_size={batch_size} concurrency={concurrency} "
        f"duration_p95_ms={duration_p95_ms:.3f} "
        f"duration_p99_ms={duration_p99_ms:.3f} "
        f"queries_max={max(query_counts)} "
        f"write_queries_max={max(write_query_counts)} "
        f"lock_wait_p95_ms={lock_wait_p95_ms:.3f} "
        f"lock_wait_p99_ms={lock_wait_p99_ms:.3f} "
        "deadlocks=0 lock_timeouts=0 "
        f"warmup_runs={_WARMUP_RUNS} measured_runs={_MEASURED_RUNS}"
    )
    _print_stage_metrics(measured_samples, batch_size=batch_size, concurrency=concurrency)

    if batch_size == 1:
        if max(query_counts) > 60:
            noisiest_sample = max(
                measured_samples,
                key=lambda sample: (sample.query_count, sample.write_query_count),
            )
            print(
                "gate_e_maintenance_query_summary "
                f"actions={noisiest_sample.action_kinds} "
                f"queries={noisiest_sample.query_count} "
                f"writes={noisiest_sample.write_query_count} "
                f"fingerprints={noisiest_sample.query_summary}"
            )
        assert duration_p95_ms <= 750
        assert duration_p99_ms <= 2_000
        assert max(query_counts) <= 60
        assert max(write_query_counts) <= 12
    elif batch_size == 100:
        if max(query_counts) > 2_500:
            noisiest_sample = max(
                worker_samples,
                key=lambda sample: sample.query_count,
            )
            print(f"gate_e_maintenance_query_summary {noisiest_sample.query_summary}")
        assert duration_p95_ms <= 60_000
        assert duration_p99_ms <= 120_000
        assert max(query_counts) <= 2_500
        assert max(write_query_counts) <= 1_200
    assert lock_wait_p95_ms <= 100
    assert lock_wait_p99_ms <= 1_000

    expected_sequence = _WARMUP_RUNS + _MEASURED_RUNS
    assert set(
        BotProfile.objects.filter(pk__in=[profile.id for profile in profiles]).values_list(
            "maintenance_sequence", flat=True
        )
    ) == {expected_sequence}
