from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay import signals as gameplay_signals
from gameplay.models import (
    BotInventoryDailyCounter,
    BotProfile,
    Building,
    InventoryItem,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
)
from gameplay.services.virtual_player_core import bootstrap
from gameplay.services.virtual_player_core.asset_policy import (
    VIRTUAL_PLAYER_USEFUL_BUILDING_KEYS,
    useful_virtual_technology_keys,
)
from gameplay.services.virtual_player_core.bootstrap_catalog import (
    clear_bootstrap_catalog_cache,
    load_bootstrap_catalog,
)
from gameplay.services.virtual_player_core.config import load_virtual_player_config, load_virtual_player_v2_config
from gameplay.services.virtual_player_core.contracts import AcceleratedGrowthOutcome
from gameplay.services.virtual_player_core.maintenance import (
    accelerate_virtual_player_growth,
    maintain_due_virtual_players,
)
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.projection import BootstrapInventoryTarget
from guests.models import GearItem, GearTemplate, Guest, GuestSkill, GuestTemplate, Skill

FIXED_NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


def _materialize_v2_plan(
    plan: bootstrap.V2BootstrapPlan,
    *,
    now: datetime = FIXED_NOW,
) -> BotProfile:
    with transaction.atomic():
        population_permit = bootstrap._issue_v2_bootstrap_population_permit(
            region=plan.region,
            prestige_band=plan.prestige_band,
        )
        return bootstrap.create_virtual_player_v2(
            plan=plan,
            population_permit=population_permit,
            now=now,
        )


def test_build_v2_bootstrap_plan_is_deterministic_and_stays_in_all_eight_bands(
    released_v2_policy,
    game_data,
) -> None:
    config = load_virtual_player_v2_config()
    assert config is not None
    expected_age_ranges = {
        "newbie": (1, 14),
        "junior": (14, 45),
        "middle": (45, 120),
        "senior": (120, 240),
        "veteran": (240, 360),
        "elite": (360, 540),
        "legend": (540, 720),
        "mythic": (720, 1080),
    }

    for band in config.bands:
        first = bootstrap.build_virtual_player_v2_bootstrap_plan(
            "north",
            band.name,
            BotProfile.Archetype.BALANCED,
            731_901,
            FIXED_NOW,
        )
        second = bootstrap.build_virtual_player_v2_bootstrap_plan(
            "north",
            band.name,
            BotProfile.Archetype.BALANCED,
            731_901,
            FIXED_NOW,
        )

        assert first == second
        assert first.bootstrap_mode == bootstrap.V2_BOOTSTRAP_MODE_POLICY_2_DEFAULT
        lower_age, upper_age = expected_age_ranges[band.name]
        assert lower_age <= first.blueprint.historical_age_days <= upper_age
        assert band.contains(first.projection.prestige)
        assert first.blueprint.reference_selection.source.value == "starter"
        assert first.blueprint.reference_selection.local_sample_count == 0
        assets = first.blueprint.assets
        assert len(assets.guests) == first.projection.guest_count
        assert max((target.level for target in assets.guests), default=0) == (
            first.projection.guest_level if first.projection.guest_count else 0
        )
        assert sum(assets.troop_counts.values()) == first.projection.troop_count
        assert sum(assets.troop_counts.values()) <= first.virtual_troop_capacity
        assert "scout" not in assets.troop_counts
        assert max(assets.building_levels.values()) == first.projection.building_level
        assert len(assets.catalog_digest) == 64
        assert all(
            0 <= offset <= first.blueprint.historical_age_days
            for offset in (
                *assets.building_created_day_offsets.values(),
                *assets.technology_reached_day_offsets.values(),
            )
        )
        with pytest.raises(TypeError):
            assets.building_levels["silver_vault"] = 999  # type: ignore[index]


@pytest.mark.django_db
def test_v2_materializer_rejects_missing_population_permit_before_writes(
    released_v2_policy,
    game_data,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "newbie",
        BotProfile.Archetype.BALANCED,
        884_200,
        FIXED_NOW,
    )
    counts_before = (
        get_user_model().objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )

    with pytest.raises(
        bootstrap.V2BootstrapError,
        match="valid population materialization permit is required",
    ):
        bootstrap.create_virtual_player_v2(plan=plan, now=FIXED_NOW)

    assert (
        get_user_model().objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before


@pytest.mark.django_db
def test_create_v2_bootstrap_materializes_high_band_and_persists_identity(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    internal_markers: list[bool] = []
    bootstrap_manor = gameplay_signals.bootstrap_manor

    def capture_internal_marker(user, *, region, initial_name):
        internal_markers.append(bool(getattr(user, "_virtual_player_internal", False)))
        return bootstrap_manor(user, region=region, initial_name=initial_name)

    monkeypatch.setattr(gameplay_signals, "bootstrap_manor", capture_internal_marker)
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "south",
        "mythic",
        BotProfile.Archetype.GUARD,
        884_201,
        FIXED_NOW,
    )

    profile = _materialize_v2_plan(plan)
    profile.refresh_from_db()

    assert profile.engine_version == 2
    assert profile.rng_version == plan.rng_version
    assert profile.plan_schema_version == plan.plan_schema_version
    assert profile.policy_version == plan.policy_version
    assert profile.policy_checksum == plan.policy_checksum
    assert profile.development_profile == plan.development_plan.to_payload()
    assert profile.v2_enrolled_at == FIXED_NOW
    assert profile.last_strength_increase_at == FIXED_NOW
    assert profile.target_prestige_band == "mythic"
    assert profile.current_prestige_band == "mythic"
    assert profile.manor.prestige >= 240_000
    assert profile.manor.user.is_active is False
    assert internal_markers == [True]
    assert profile.manor.created_at == FIXED_NOW - timedelta(days=plan.blueprint.historical_age_days)
    assert profile.manor.initial_peace_shield_granted_at is None
    assert list(
        profile.manor.inventory_items.order_by("template__key").values_list(
            "template__key",
            "quantity",
            "storage_location",
        )
    ) == sorted(
        (
            target.template_key,
            target.quantity,
            target.storage_location,
        )
        for target in plan.blueprint.assets.inventory
    )


@pytest.mark.django_db
def test_create_v2_bootstrap_rolls_back_everything_when_enrollment_fails(
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "east",
        "elite",
        BotProfile.Archetype.RICH,
        991_031,
        FIXED_NOW,
    )
    user_model = get_user_model()
    counts_before = (
        user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )

    def fail_enrollment(*args, **kwargs):
        raise RuntimeError("forced enrollment failure")

    monkeypatch.setattr(bootstrap.profile_store, "enroll_profile_v2", fail_enrollment)

    with pytest.raises(RuntimeError, match="forced enrollment failure"):
        _materialize_v2_plan(plan)

    assert (
        user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before


@pytest.mark.django_db
def test_legacy_maintenance_never_falls_back_for_v2_profile(
    released_v2_policy,
    game_data,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "west",
        "mythic",
        BotProfile.Archetype.BALANCED,
        991_032,
        FIXED_NOW,
    )
    profile = _materialize_v2_plan(plan)
    due_at = FIXED_NOW - timedelta(minutes=1)
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=due_at)
    snapshot = (
        int(profile.manor.prestige),
        profile.current_prestige_band,
        int(profile.growth_stage),
    )

    assert maintain_due_virtual_players(now=FIXED_NOW, limit=10) == 0
    assert accelerate_virtual_player_growth(profile.id, now=FIXED_NOW) is AcceleratedGrowthOutcome.PAUSED

    profile.refresh_from_db()
    profile.manor.refresh_from_db()
    assert (
        int(profile.manor.prestige),
        profile.current_prestige_band,
        int(profile.growth_stage),
    ) == snapshot
    assert profile.next_growth_at == due_at


@pytest.mark.django_db
def test_v2_bootstrap_planning_is_read_only_and_bounded(
    released_v2_policy,
    game_data,
) -> None:
    with CaptureQueriesContext(connection) as captured:
        plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
            "north",
            "legend",
            BotProfile.Archetype.DOJO,
            991_040,
            FIXED_NOW,
        )

    write_prefixes = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    writes = [
        query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(write_prefixes)
    ]
    assert writes == []
    assert len(captured) <= 20
    assert plan.blueprint.assets.catalog_digest


@pytest.mark.django_db
def test_unlocked_bootstrap_catalog_is_cached_but_locked_reads_bypass_cache(game_data) -> None:
    clear_bootstrap_catalog_cache()
    config = load_virtual_player_config()
    assert config is not None

    with CaptureQueriesContext(connection) as first_read:
        first = load_bootstrap_catalog(config)
    with CaptureQueriesContext(connection) as cached_read:
        cached = load_bootstrap_catalog(config)
    with transaction.atomic(), CaptureQueriesContext(connection) as locked_read:
        locked = load_bootstrap_catalog(config, lock=True)

    assert first == cached
    assert locked == first
    assert len(first_read) > 0
    assert len(cached_read) == 0
    assert len(locked_read) > 0
    clear_bootstrap_catalog_cache()


@pytest.mark.django_db
def test_v2_bootstrap_excludes_event_boss_skills_from_virtual_guests(
    released_v2_policy,
    game_data,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "middle",
        BotProfile.Archetype.BALANCED,
        991_041,
        FIXED_NOW,
    )

    catalog = load_bootstrap_catalog(load_virtual_player_config())
    catalog_skill_keys = {entry.key for entry in catalog.skills}
    selected_skill_keys = {key for target in plan.blueprint.assets.guests for key in target.skill_keys}

    assert "gl_top_qiankun_holy_flame" not in catalog_skill_keys
    assert not any(key.startswith("gl_top_") for key in selected_skill_keys)
    assert selected_skill_keys <= catalog_skill_keys


@pytest.mark.django_db
def test_v2_bootstrap_materializes_exact_blueprint_assets_in_all_eight_bands(
    released_v2_policy,
    game_data,
) -> None:
    ItemTemplate.objects.create(
        key="v2_bootstrap_common_stock",
        name="V2 bootstrap common stock",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="black",
        tradeable=True,
        price=1,
        storage_space=1,
    )
    config = load_virtual_player_v2_config()
    assert config is not None

    for index, band in enumerate(config.bands):
        plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
            "west",
            band.name,
            BotProfile.Archetype.BALANCED,
            992_000 + index,
            FIXED_NOW,
        )
        profile = _materialize_v2_plan(plan)
        manor = profile.manor
        assets = plan.blueprint.assets
        account_created_at = FIXED_NOW - timedelta(days=plan.blueprint.historical_age_days)

        assert assets.retainer_count == 0
        assert all(
            assets.building_levels[key] == 1
            for key in assets.building_levels
            if key not in VIRTUAL_PLAYER_USEFUL_BUILDING_KEYS
        )
        useful_technology_keys = useful_virtual_technology_keys(
            troop_class for troop_class, _ratio in plan.development_plan.troop_mix
        )
        assert all(
            assets.technology_levels[key] == 0 for key in assets.technology_levels if key not in useful_technology_keys
        )

        assert dict(
            Building.objects.filter(manor=manor).values_list(
                "building_type__key",
                "level",
            )
        ) == dict(assets.building_levels)
        assert dict(
            Building.objects.filter(manor=manor).values_list(
                "building_type__key",
                "created_at",
            )
        ) == {
            key: account_created_at + timedelta(days=offset)
            for key, offset in assets.building_created_day_offsets.items()
        }
        assert dict(
            PlayerTechnology.objects.filter(manor=manor).values_list(
                "tech_key",
                "level",
            )
        ) == dict(assets.technology_levels)
        assert dict(
            PlayerTroop.objects.filter(manor=manor).values_list(
                "troop_template__key",
                "count",
            )
        ) == dict(assets.troop_counts)
        assert manor.retainer_count == assets.retainer_count
        assert manor.silver == assets.silver
        assert manor.grain == assets.grain

        guests = list(Guest.objects.filter(manor=manor).order_by("id"))
        assert [guest.template.key for guest in guests] == [target.template_key for target in assets.guests]
        assert [guest.level for guest in guests] == [target.level for target in assets.guests]
        assert [guest.created_at for guest in guests] == [
            account_created_at + timedelta(days=target.created_day_offset) for target in assets.guests
        ]
        for guest, target in zip(guests, assets.guests, strict=True):
            assert list(
                GearItem.objects.filter(guest=guest).order_by("id").values_list("template__key", "acquired_at")
            ) == [
                (
                    key,
                    account_created_at + timedelta(days=offset),
                )
                for key, offset in zip(
                    target.gear_template_keys,
                    target.gear_acquired_day_offsets,
                    strict=True,
                )
            ]
            assert list(
                GuestSkill.objects.filter(guest=guest).order_by("id").values_list("skill__key", "learned_at")
            ) == [
                (
                    key,
                    account_created_at + timedelta(days=offset),
                )
                for key, offset in zip(
                    target.skill_keys,
                    target.skill_learned_day_offsets,
                    strict=True,
                )
            ]
        assert list(
            InventoryItem.objects.filter(manor=manor)
            .order_by("template__key", "storage_location")
            .values_list(
                "template__key",
                "quantity",
                "storage_location",
                "created_at",
            )
        ) == sorted(
            (
                target.template_key,
                target.quantity,
                target.storage_location,
                account_created_at + timedelta(days=target.acquired_day_offset),
            )
            for target in assets.inventory
        )


@pytest.mark.django_db
def test_v2_materialization_rejects_catalog_drift_before_creating_a_user(
    released_v2_policy,
    game_data,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "north",
        "middle",
        BotProfile.Archetype.BALANCED,
        993_001,
        FIXED_NOW,
    )
    template_key = plan.blueprint.assets.guests[0].template_key
    guest_template = GuestTemplate.objects.get(key=template_key)
    guest_template.base_hp = int(guest_template.base_hp) + 1
    guest_template.save(update_fields=["base_hp"])
    user_model = get_user_model()
    counts_before = (
        user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    )

    with pytest.raises(bootstrap.V2BootstrapError, match="catalog changed"):
        _materialize_v2_plan(plan)

    assert (
        user_model.objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
    ) == counts_before


@pytest.mark.django_db
def test_v2_materialization_rolls_back_when_gear_exceeds_slot_capacity(
    released_v2_policy,
    game_data,
) -> None:
    gear_keys = tuple(f"v2_device_{index}" for index in range(4))
    GearTemplate.objects.bulk_create(
        [
            GearTemplate(
                key=key,
                name=key,
                slot="device",
                rarity="black",
            )
            for key in gear_keys
        ]
    )
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "east",
        "middle",
        BotProfile.Archetype.GUARD,
        993_002,
        FIXED_NOW,
    )
    first_guest = plan.blueprint.assets.guests[0]
    invalid_guest = replace(
        first_guest,
        gear_template_keys=gear_keys,
        gear_acquired_day_offsets=(first_guest.created_day_offset,) * len(gear_keys),
    )
    assets = replace(
        plan.blueprint.assets,
        guests=(invalid_guest, *plan.blueprint.assets.guests[1:]),
    )
    plan = replace(plan, blueprint=replace(plan.blueprint, assets=assets))
    user_count = get_user_model().objects.count()

    with pytest.raises(bootstrap.V2BootstrapError, match="slot capacity"):
        _materialize_v2_plan(plan)

    assert get_user_model().objects.count() == user_count


@pytest.mark.django_db
def test_v2_materialization_rolls_back_when_skills_exceed_capacity(
    released_v2_policy,
    game_data,
) -> None:
    skill_keys = tuple(f"v2_skill_{index}" for index in range(4))
    Skill.objects.bulk_create(
        [
            Skill(
                key=key,
                name=key,
                kind="passive",
                rarity="black",
            )
            for key in skill_keys
        ]
    )
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "east",
        "middle",
        BotProfile.Archetype.DOJO,
        993_005,
        FIXED_NOW,
    )
    first_guest = plan.blueprint.assets.guests[0]
    invalid_guest = replace(
        first_guest,
        skill_keys=skill_keys,
        skill_learned_day_offsets=(first_guest.created_day_offset,) * len(skill_keys),
    )
    assets = replace(
        plan.blueprint.assets,
        guests=(invalid_guest, *plan.blueprint.assets.guests[1:]),
    )
    plan = replace(plan, blueprint=replace(plan.blueprint, assets=assets))
    user_count = get_user_model().objects.count()

    with pytest.raises(bootstrap.V2BootstrapError, match="skill capacity"):
        _materialize_v2_plan(plan)

    assert get_user_model().objects.count() == user_count


@pytest.mark.django_db
def test_v2_materialization_requires_full_inventory_cap_reservation(
    released_v2_policy,
    game_data,
) -> None:
    rare_template = ItemTemplate.objects.create(
        key="v2_bootstrap_rare_cap",
        name="V2 bootstrap rare cap",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="purple",
        tradeable=True,
        price=1,
        storage_space=1,
    )
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "south",
        "middle",
        BotProfile.Archetype.RICH,
        993_003,
        FIXED_NOW,
    )
    rare_target = BootstrapInventoryTarget(
        template_key=rare_template.key,
        quantity=1,
        acquired_day_offset=plan.blueprint.historical_age_days,
    )
    assets = replace(
        plan.blueprint.assets,
        inventory=(*plan.blueprint.assets.inventory, rare_target),
    )
    plan = replace(plan, blueprint=replace(plan.blueprint, assets=assets))
    counter_date = timezone.localtime(FIXED_NOW).date()
    BotInventoryDailyCounter.objects.create(
        category="rare",
        counter_date=counter_date,
        quantity=8,
    )
    counts_before = (
        get_user_model().objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
        InventoryItem.objects.count(),
    )

    with pytest.raises(bootstrap.V2BootstrapError, match="inventory daily cap"):
        _materialize_v2_plan(plan)

    assert (
        get_user_model().objects.count(),
        Manor.objects.count(),
        BotProfile.objects.count(),
        InventoryItem.objects.count(),
    ) == counts_before
    assert (
        BotInventoryDailyCounter.objects.get(
            category="rare",
            counter_date=counter_date,
        ).quantity
        == 8
    )


@pytest.mark.django_db
def test_v2_materialization_stays_within_query_and_write_budgets(
    released_v2_policy,
    game_data,
) -> None:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        "south",
        "mythic",
        BotProfile.Archetype.GUARD,
        993_004,
        FIXED_NOW,
    )

    with CaptureQueriesContext(connection) as captured:
        _materialize_v2_plan(plan)

    write_prefixes = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    writes = [
        query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(write_prefixes)
    ]
    assert len(captured) <= 80
    assert len(writes) <= 25
