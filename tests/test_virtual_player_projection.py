from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError

import pytest

import gameplay.services.virtual_player_core.projection as projection_module
from gameplay.services.virtual_player_core.projection import (
    PRESTIGE_BANDS,
    BootstrapAssetTargets,
    BootstrapBlueprint,
    DevelopmentIntent,
    GuestHealingCandidate,
    ProjectionRuleError,
    ReferenceCandidate,
    ReferenceSelection,
    ReferenceSource,
    SampleTier,
    StrengthSummary,
    clip_p5_p95,
    composite_growth_bps,
    crosses_at_most_one_band,
    project_guest_healing_development_intent,
    project_troop_recruitment_development_intent,
    safety_rule_for_sample_count,
    sample_tier_for_count,
    select_development_intent,
    select_guest_healing_candidate,
    select_reference,
    validate_controlled_band_transition,
)
from gameplay.services.virtual_player_core.random_context import RandomContext


class _FixedRandom:
    def __init__(self, *, roll: float, index: int) -> None:
        self.roll = roll
        self.index = index

    def random(self) -> float:
        return self.roll

    def randrange(self, stop: int) -> int:
        return self.index % stop


class _RecordingContext:
    def __init__(self, *, bucket: int = 0, roll: float = 0.0, index: int = 0) -> None:
        self.bucket_value = bucket
        self.roll = roll
        self.index = index
        self.bucket_calls: list[tuple[str, object, int]] = []
        self.random_calls: list[tuple[str, object]] = []

    def bucket(self, *, domain: str, discriminator: object, bucket_count: int = 100) -> int:
        self.bucket_calls.append((domain, discriminator, bucket_count))
        return self.bucket_value % bucket_count

    def random(self, *, domain: str, discriminator: object) -> _FixedRandom:
        self.random_calls.append((domain, discriminator))
        return _FixedRandom(roll=self.roll, index=self.index)


def _context(**overrides) -> RandomContext:
    values = {
        "rng_version": 1,
        "growth_seed": 271828,
        "engine_version": 2,
        "plan_schema_version": 1,
        "policy_version": 1,
        "maintenance_sequence": 4,
    }
    values.update(overrides)
    return RandomContext(**values)


def _strength(value: float, **components: float) -> StrengthSummary:
    return StrengthSummary(composite=value, components=components or {"power": value})


def _candidate(
    business_key: str,
    value: float,
    *,
    prestige_band: str = "middle",
    features: dict[str, float] | None = None,
) -> ReferenceCandidate:
    return ReferenceCandidate(
        business_key=business_key,
        prestige_band=prestige_band,
        strength=_strength(value),
        features=features or {"joint_score": value},
    )


def _intent(
    business_key: str,
    utility_score: float,
    *,
    source_band: str = "middle",
    target_band: str = "middle",
    violations: tuple[str, ...] = (),
) -> DevelopmentIntent:
    return DevelopmentIntent(
        business_key=business_key,
        action_kind="training",
        source_prestige_band=source_band,
        target_prestige_band=target_band,
        strength_before=_strength(100),
        strength_after=_strength(101),
        utility_score=utility_score,
        constraint_violations=violations,
    )


def _assets() -> BootstrapAssetTargets:
    return BootstrapAssetTargets(
        building_levels={"silver_vault": 1},
        technology_levels={},
        guests=(),
        retainer_count=0,
        troop_counts={},
        inventory=(),
        silver=1,
        grain=1,
        catalog_digest="0" * 64,
    )


def test_projection_values_are_frozen_and_deeply_immutable() -> None:
    components = {"troops": 20.0, "buildings": 10.0}
    features = {"troops": 4.0, "buildings": 2.0}
    strength = StrengthSummary(composite=30, components=components)
    candidate = ReferenceCandidate("human-1", "middle", strength, features)

    components["troops"] = 999
    features["troops"] = 999

    assert tuple(strength.components) == ("buildings", "troops")
    assert strength.components["troops"] == 20
    assert candidate.features["troops"] == 4
    with pytest.raises(TypeError):
        strength.components["troops"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        candidate.features["troops"] = 1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        candidate.business_key = "changed"  # type: ignore[misc]


def test_blueprint_fails_closed_when_composite_or_component_exceeds_cap() -> None:
    selection = ReferenceSelection(
        prestige_band="middle",
        tier=SampleTier.NO_REFERENCE,
        source=ReferenceSource.STARTER,
        local_sample_count=0,
        anchor=None,
        cap=_strength(100, attack=50, defense=75),
        nearest_candidate_keys=(),
    )

    valid = BootstrapBlueprint(
        business_key="bot-1",
        prestige_band="middle",
        historical_age_days=80,
        target_strength=_strength(100, attack=50, defense=75),
        reference_selection=selection,
        assets=_assets(),
    )

    assert valid.target_strength.composite == 100
    with pytest.raises(ProjectionRuleError, match="composite"):
        BootstrapBlueprint(
            "bot-2",
            "middle",
            80,
            _strength(101, attack=50, defense=75),
            selection,
            _assets(),
        )
    with pytest.raises(ProjectionRuleError, match="components"):
        BootstrapBlueprint(
            "bot-3",
            "middle",
            80,
            _strength(100, attack=51, defense=75),
            selection,
            _assets(),
        )


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, SampleTier.NO_REFERENCE),
        (1, SampleTier.SPARSE),
        (4, SampleTier.SPARSE),
        (5, SampleTier.LIMITED),
        (29, SampleTier.LIMITED),
        (30, SampleTier.SUFFICIENT),
    ],
)
def test_sample_tier_boundaries_are_frozen(count: int, expected: SampleTier) -> None:
    assert sample_tier_for_count(count) is expected


def test_strength_safety_rules_match_the_frozen_gate_values() -> None:
    assert [
        (
            safety_rule_for_sample_count(count).cap_quantile,
            safety_rule_for_sample_count(count).composite_cap_ratio,
            safety_rule_for_sample_count(count).component_cap_ratio,
            safety_rule_for_sample_count(count).positive_jitter_bps,
            safety_rule_for_sample_count(count).actions_per_24h,
            safety_rule_for_sample_count(count).growth_bps_per_24h,
        )
        for count in (0, 1, 5, 30)
    ] == [
        ("starter", 0.90, 0.90, 0, 0, 0),
        ("p50", 1.05, 1.10, 0, 1, 300),
        ("p75", 1.10, 1.15, 200, 2, 500),
        ("p95", 1.15, 1.20, 500, 4, 1000),
    ]


@pytest.mark.parametrize(
    ("count", "quantile_value", "composite_ratio", "component_ratio"),
    [
        (4, 2, 1.05, 1.10),
        (5, 4, 1.10, 1.15),
        (30, 29, 1.15, 1.20),
    ],
)
def test_reference_caps_use_nearest_rank_percentiles(
    count: int,
    quantile_value: int,
    composite_ratio: float,
    component_ratio: float,
) -> None:
    candidates = [
        _candidate(f"human-{index:02d}", float(index), features={"joint_score": float(index)})
        for index in range(1, count + 1)
    ]

    result = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"joint_score": 1},
        starter_strength=_strength(1),
        local_candidates=candidates,
        nearest_k=1,
    )

    assert result.cap.composite == pytest.approx(quantile_value * composite_ratio)
    assert result.cap.components["power"] == pytest.approx(quantile_value * component_ratio)


def test_p5_p95_clipping_uses_nearest_rank_bounds() -> None:
    reference_values = list(range(1, 101))

    assert clip_p5_p95(-0.0, reference_values) == 5
    assert clip_p5_p95(50, reference_values) == 50
    assert clip_p5_p95(1_000, reference_values) == 95


def test_reference_selection_is_order_independent_and_uses_reference_anchor_stream() -> None:
    candidates = [
        _candidate("human-a", 10, features={"prestige": 0, "troops": 0}),
        _candidate("human-b", 20, features={"prestige": 1, "troops": 1}),
        _candidate("human-c", 30, features={"prestige": 9, "troops": 9}),
        _candidate(
            "other-band",
            999,
            prestige_band="senior",
            features={"prestige": 1, "troops": 1},
        ),
    ]
    first_context = _RecordingContext(bucket=0)
    second_context = _RecordingContext(bucket=0)

    first = select_reference(
        context=first_context,  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"troops": 1.1, "prestige": 0.9},
        starter_strength=_strength(5),
        local_candidates=candidates,
        nearest_k=2,
    )
    second = select_reference(
        context=second_context,  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"prestige": 0.9, "troops": 1.1},
        starter_strength=_strength(5),
        local_candidates=reversed(candidates),
        nearest_k=2,
    )

    assert first == second
    assert first.local_sample_count == 3
    assert first.nearest_candidate_keys == ("human-b", "human-a")
    assert first.anchor is not None and first.anchor.business_key == "human-b"
    assert first_context.bucket_calls[0][0] == "reference_anchor"
    assert first_context.bucket_calls[0][2] == 2


def test_reference_selection_is_stable_with_the_versioned_random_context() -> None:
    candidates = [_candidate(f"human-{index}", float(index)) for index in range(1, 7)]

    first = select_reference(
        context=_context(),
        prestige_band="middle",
        target_features={"joint_score": 3.5},
        starter_strength=_strength(1),
        local_candidates=candidates,
    )
    second = select_reference(
        context=_context(),
        prestige_band="middle",
        target_features={"joint_score": 3.5},
        starter_strength=_strength(1),
        local_candidates=reversed(candidates),
    )

    assert first == second


def test_reference_selection_uses_total_local_count_when_candidates_are_bounded() -> None:
    candidate = _candidate("human-sampled", 10)

    result = select_reference(
        context=_context(),
        prestige_band="middle",
        target_features={"joint_score": 10},
        starter_strength=_strength(1),
        local_candidates=(candidate,),
        local_sample_count=30,
    )

    assert result.local_sample_count == 30
    assert result.tier is SampleTier.SUFFICIENT
    assert result.source is ReferenceSource.LOCAL


def test_zero_local_samples_borrow_only_a_discounted_global_same_band_anchor() -> None:
    starter = _strength(100, attack=50, defense=120)
    global_same_band = ReferenceCandidate(
        "global-middle",
        "middle",
        _strength(80, attack=100, defense=40),
        {"joint_score": 8},
    )
    global_other_band = ReferenceCandidate(
        "global-senior",
        "senior",
        _strength(1_000, attack=1_000, defense=1_000),
        {"joint_score": 8},
    )

    result = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"joint_score": 8},
        starter_strength=starter,
        local_candidates=(),
        global_candidates=(global_other_band, global_same_band),
        global_same_band_cap=_strength(80, attack=100, defense=40),
    )

    assert result.tier is SampleTier.NO_REFERENCE
    assert result.local_sample_count == 0
    assert result.source is ReferenceSource.GLOBAL_SAME_BAND
    assert result.anchor is global_same_band
    assert result.cap.composite == 72
    assert dict(result.cap.components) == {"attack": 45, "defense": 36}


def test_discounted_global_cap_preserves_an_indivisible_core_building() -> None:
    starter = _strength(100, core_building_level=2, guest_count=2)
    global_same_band = ReferenceCandidate(
        "global-newbie",
        "newbie",
        _strength(80, core_building_level=1, guest_count=1),
        {"core_building_level": 1, "guest_count": 1},
    )

    result = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        prestige_band="newbie",
        target_features={"core_building_level": 2, "guest_count": 2},
        starter_strength=starter,
        local_candidates=(),
        global_candidates=(global_same_band,),
        global_same_band_cap=global_same_band.strength,
    )

    assert result.source is ReferenceSource.GLOBAL_SAME_BAND
    assert result.cap.components["core_building_level"] == 1
    assert result.cap.components["guest_count"] == pytest.approx(0.9)


def test_zero_local_global_cap_is_independent_of_anchor_selection() -> None:
    candidates = (
        _candidate("low", 10, features={"joint_score": 1}),
        _candidate("high", 1_000, features={"joint_score": 100}),
    )
    common = {
        "prestige_band": "middle",
        "starter_strength": _strength(200),
        "local_candidates": (),
        "global_candidates": candidates,
        "global_same_band_cap": _strength(150),
        "nearest_k": 1,
    }

    low_anchor = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        target_features={"joint_score": 1},
        **common,
    )
    high_anchor = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        target_features={"joint_score": 100},
        **common,
    )

    assert low_anchor.anchor is not None and low_anchor.anchor.business_key == "low"
    assert high_anchor.anchor is not None and high_anchor.anchor.business_key == "high"
    assert low_anchor.cap == high_anchor.cap == _strength(135)


def test_zero_local_global_candidates_without_a_cohort_cap_fall_back_to_starter() -> None:
    result = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"joint_score": 1},
        starter_strength=_strength(100),
        local_candidates=(),
        global_candidates=(_candidate("global", 1_000),),
    )

    assert result.source is ReferenceSource.STARTER
    assert result.anchor is None
    assert result.cap == _strength(90)


def test_zero_local_samples_fall_back_to_discounted_starter_without_a_global_same_band() -> None:
    result = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"joint_score": 8},
        starter_strength=_strength(100, attack=50),
        local_candidates=(),
        global_candidates=(_candidate("global-senior", 1_000, prestige_band="senior"),),
    )

    assert result.source is ReferenceSource.STARTER
    assert result.anchor is None
    assert result.cap.composite == 90
    assert result.cap.components["attack"] == 45


def test_sparse_local_sample_cannot_be_replaced_or_strengthened_by_global_candidates() -> None:
    result = select_reference(
        context=_RecordingContext(),  # type: ignore[arg-type]
        prestige_band="middle",
        target_features={"joint_score": 10},
        starter_strength=_strength(100),
        local_candidates=(_candidate("local", 10),),
        global_candidates=(_candidate("global", 10_000),),
    )

    assert result.tier is SampleTier.SPARSE
    assert result.source is ReferenceSource.LOCAL
    assert result.anchor is not None and result.anchor.business_key == "local"
    assert result.cap.composite == 10.5
    assert result.cap.components["power"] == 11


def test_duplicate_reference_business_keys_fail_closed() -> None:
    with pytest.raises(ProjectionRuleError, match="business_key"):
        select_reference(
            context=_RecordingContext(),  # type: ignore[arg-type]
            prestige_band="middle",
            target_features={"joint_score": 1},
            starter_strength=_strength(1),
            local_candidates=(_candidate("duplicate", 1), _candidate("duplicate", 2)),
        )


def test_development_intent_filters_hard_constraints_before_utility_ranking() -> None:
    context = _RecordingContext()
    invalid = _intent("invalid", 1_000, violations=("insufficient_resources",))
    two_band_jump = _intent("jump", 900, source_band="middle", target_band="veteran")
    best_legal = _intent("best", 20)
    other_legal = _intent("other", 10)

    selected = select_development_intent(
        (invalid, two_band_jump, other_legal, best_legal),
        context=context,  # type: ignore[arg-type]
        optimization_bias=1,
        top_k=3,
    )

    assert selected is best_legal
    assert context.random_calls[0][0] == "schedule"


def test_troop_recruitment_projection_uses_canonical_troop_weight() -> None:
    strength_before = StrengthSummary(
        composite=140,
        components={
            "arena_lineup_power": 100,
            "core_building_level": 2,
            "guest_count": 2,
            "max_guest_level": 5,
            "prestige": 400,
            "troop_total": 20,
        },
    )

    intent = project_troop_recruitment_development_intent(
        troop_key="dao_ke",
        quantity=3,
        prestige_band="newbie",
        strength_before=strength_before,
        utility_score=0.5,
    )

    assert intent.business_key == "troop_recruitment:dao_ke:3"
    assert intent.action_kind == "troop_recruitment"
    assert intent.strength_before == strength_before
    assert intent.strength_after.composite == 146
    assert intent.strength_after.components["troop_total"] == 23
    assert intent.strength_after.components["arena_lineup_power"] == 100
    assert strength_before.components["troop_total"] == 20


def test_guest_healing_selection_prioritizes_injury_tier_and_missing_hp() -> None:
    context = _RecordingContext(index=1)
    candidates = (
        GuestHealingCandidate(1, 11, "small", "core", False, 1, 100),
        GuestHealingCandidate(2, 11, "small", "bench", True, 1, 100),
        GuestHealingCandidate(3, 11, "small", "core", True, 90, 100),
        GuestHealingCandidate(4, 11, "small", "core", True, 10, 100),
    )

    selected = select_guest_healing_candidate(
        reversed(candidates),
        context=context,  # type: ignore[arg-type]
    )

    assert selected is candidates[3]
    assert context.random_calls == []


def test_guest_healing_exact_ties_use_versioned_stable_context() -> None:
    context = _RecordingContext(index=1)
    first = GuestHealingCandidate(10, 20, "small", "core", True, 10, 100)
    second = GuestHealingCandidate(11, 20, "small", "core", True, 10, 100)

    selected = select_guest_healing_candidate(
        (second, first),
        context=context,  # type: ignore[arg-type]
    )

    assert selected is second
    assert context.random_calls[0][0] == "roster"
    assert context.random_calls[0][1]["candidates"] == [
        first.business_key,
        second.business_key,
    ]


def test_guest_healing_projection_preserves_permanent_strength() -> None:
    strength = StrengthSummary(
        composite=140,
        components={
            "arena_lineup_power": 100,
            "core_building_level": 2,
            "guest_count": 2,
            "max_guest_level": 5,
            "prestige": 400,
            "troop_total": 20,
        },
    )
    candidate = GuestHealingCandidate(7, 9, "medicine", "core", True, 10, 100)

    intent = project_guest_healing_development_intent(
        candidate=candidate,
        prestige_band="newbie",
        strength_before=strength,
    )

    assert intent.action_kind == "guest_healing"
    assert intent.business_key == candidate.business_key
    assert intent.strength_before is strength
    assert intent.strength_after is strength


def test_low_optimization_bias_can_choose_only_within_the_stable_top_k() -> None:
    context = _RecordingContext(roll=0.9, index=1)
    candidates = (_intent("first", 30), _intent("second", 20), _intent("third", 10))

    selected = select_development_intent(
        reversed(candidates),
        context=context,  # type: ignore[arg-type]
        optimization_bias=0,
        top_k=2,
    )

    assert selected is candidates[1]
    discriminator = context.random_calls[0][1]
    assert isinstance(discriminator, dict)
    assert [row["business_key"] for row in discriminator["candidates"]] == [
        "first",
        "second",
    ]


def test_development_intent_ties_use_the_business_key_and_all_invalid_returns_none() -> None:
    context = _RecordingContext()

    tied = select_development_intent(
        (_intent("z", 10), _intent("a", 10)),
        context=context,  # type: ignore[arg-type]
        optimization_bias=1,
    )
    none_selected = select_development_intent(
        (_intent("invalid", 100, violations=("cap",)),),
        context=context,  # type: ignore[arg-type]
        optimization_bias=1,
    )

    assert tied is not None and tied.business_key == "a"
    assert none_selected is None


def test_composite_growth_bps_uses_a_floor_and_rounds_positive_growth_up() -> None:
    assert composite_growth_bps(100, 101) == 100
    assert composite_growth_bps(3, 3.0001) == 1
    assert composite_growth_bps(0, 0.0001) == 1
    assert composite_growth_bps(100, 99) == 0


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        ("middle", "middle", True),
        ("middle", "senior", True),
        ("middle", "junior", True),
        ("middle", "veteran", False),
        ("unknown", "middle", False),
        ("middle", "unknown", False),
    ],
)
def test_controlled_actions_cross_at_most_one_known_band(source: str, target: str, allowed: bool) -> None:
    assert PRESTIGE_BANDS == (
        "newbie",
        "junior",
        "middle",
        "senior",
        "veteran",
        "elite",
        "legend",
        "mythic",
    )
    assert crosses_at_most_one_band(source, target) is allowed
    if allowed:
        validate_controlled_band_transition(source, target)
    else:
        with pytest.raises(ProjectionRuleError, match="at most one adjacent"):
            validate_controlled_band_transition(source, target)


def test_projection_module_has_no_django_or_orm_imports() -> None:
    tree = ast.parse(inspect.getsource(projection_module))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any(module == "django" or module.startswith("django.") for module in imported_modules)
    assert not any(module == "gameplay.models" or module.startswith("gameplay.models.") for module in imported_modules)
