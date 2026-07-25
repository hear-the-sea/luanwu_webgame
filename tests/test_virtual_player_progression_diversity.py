from __future__ import annotations

import random
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.models import BotProfile, InventoryItem, ItemTemplate, PlayerTroop
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_players import (
    BotProjectionConfig,
    _projection_for_band,
    _projection_from_real_players,
    create_virtual_player,
    maintain_due_virtual_players,
)
from guests.models import GearItem, GearSlot, GearTemplate, GuestArchetype, GuestRarity, GuestTemplate, Skill, SkillKind


@pytest.mark.django_db
def test_bot_profile_starts_with_an_empty_inventory_template_pool(django_user_model):
    user = django_user_model.objects.create_user(username="bot_inventory_pool", password="pass123")
    manor = ensure_manor(user)
    now = timezone.now()

    profile = BotProfile.objects.create(
        manor=manor,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=1,
        next_growth_at=now,
        abandon_at=now,
        retire_at=now,
    )

    assert profile.inventory_template_keys == []


@pytest.mark.django_db
def test_real_player_projection_uses_median_prestige_not_a_single_random_manor(django_user_model):
    for index, prestige in enumerate([600, 1_000, 1_800]):
        user = django_user_model.objects.create_user(username=f"projection_median_{index}", password="pass123")
        manor = ensure_manor(user)
        manor.region = "north"
        manor.prestige = prestige
        manor.save(update_fields=["region", "prestige"])

    projection = _projection_from_real_players(
        region="north",
        low=500,
        high=2_000,
        rng=random.Random(1),
    )

    assert projection is not None
    assert 950 <= projection.prestige <= 1_050


@pytest.mark.django_db
def test_real_player_projection_excludes_players_outside_active_sample_window(settings, django_user_model):
    now = timezone.now()
    active_user = django_user_model.objects.create_user(username="projection_active", password="pass123")
    active_manor = ensure_manor(active_user)
    active_manor.region = "north"
    active_manor.prestige = 800
    active_manor.last_active_at = now - timedelta(days=2)
    active_manor.save(update_fields=["region", "prestige", "last_active_at"])

    inactive_user = django_user_model.objects.create_user(username="projection_inactive", password="pass123")
    inactive_manor = ensure_manor(inactive_user)
    inactive_manor.region = "north"
    inactive_manor.prestige = 1_900
    inactive_manor.last_active_at = now - timedelta(days=31)
    inactive_manor.save(update_fields=["region", "prestige", "last_active_at"])
    config = {
        "projection": {
            "active_sample_days": 30,
            "regional_min_sample_size": 1,
            "real_projection_sample_size": 25,
            "real_projection_jitter_bps": 0,
        }
    }

    projection = _projection_from_real_players(
        region="north",
        low=500,
        high=2_000,
        rng=random.Random(1),
        config=config,
        now=now,
        sample_seed=1,
    )

    assert projection is not None
    assert projection.prestige == 800


@pytest.mark.django_db
def test_real_player_projection_falls_back_when_regional_sample_is_too_small(settings, django_user_model):
    now = timezone.now()
    for index, (region, prestige) in enumerate(
        [("north", 600), ("north", 650), ("south", 1_400), ("east", 1_450), ("west", 1_500)]
    ):
        user = django_user_model.objects.create_user(username=f"projection_fallback_{index}", password="pass123")
        manor = ensure_manor(user)
        manor.region = region
        manor.prestige = prestige
        manor.last_active_at = now
        manor.save(update_fields=["region", "prestige", "last_active_at"])
    config = {
        "projection": {
            "active_sample_days": 30,
            "regional_min_sample_size": 3,
            "real_projection_sample_size": 25,
            "real_projection_jitter_bps": 0,
        }
    }

    projection = _projection_from_real_players(
        region="north",
        low=500,
        high=2_000,
        rng=random.Random(1),
        config=config,
        now=now,
        sample_seed=1,
    )

    assert projection is not None
    assert projection.prestige == 1_400


@pytest.mark.django_db
def test_population_projection_uses_configured_seed_stable_strength_quantiles(django_user_model):
    now = timezone.now()
    for index, prestige in enumerate([600, 1_000, 1_800]):
        user = django_user_model.objects.create_user(username=f"projection_quantile_{index}", password="pass123")
        manor = ensure_manor(user)
        manor.region = "north"
        manor.prestige = prestige
        manor.last_active_at = now
        manor.save(update_fields=["region", "prestige", "last_active_at"])
    base_projection = {
        "active_sample_days": 30,
        "regional_min_sample_size": 1,
        "real_projection_sample_size": 25,
        "real_projection_jitter_bps": 0,
    }

    weak = _projection_for_band(
        "junior",
        500,
        2_000,
        random.Random(1),
        region="north",
        config={"projection": {**base_projection, "strength_quantile_weights": {"p25": 1, "p50": 0, "p75": 0}}},
        sample_seed=101,
    )
    strong = _projection_for_band(
        "junior",
        500,
        2_000,
        random.Random(1),
        region="north",
        config={"projection": {**base_projection, "strength_quantile_weights": {"p25": 0, "p50": 0, "p75": 1}}},
        sample_seed=202,
    )

    assert weak.prestige == 600
    assert strong.prestige == 1_800


@pytest.mark.django_db
def test_real_projection_uses_summed_army_size(django_user_model):
    now = timezone.now()
    user = django_user_model.objects.create_user(username="projection_troop_scale", password="pass123")
    manor = ensure_manor(user)
    manor.region = "north"
    manor.prestige = 900
    manor.last_active_at = now
    manor.save(update_fields=["region", "prestige", "last_active_at"])
    first = TroopTemplate.objects.create(key="projection_guard_a", name="投影护院甲")
    second = TroopTemplate.objects.create(key="projection_guard_b", name="投影护院乙")
    PlayerTroop.objects.create(manor=manor, troop_template=first, count=100)
    PlayerTroop.objects.create(manor=manor, troop_template=second, count=150)

    projection = _projection_from_real_players(
        region="north",
        low=500,
        high=2_000,
        rng=random.Random(1),
        config={
            "projection": {
                "active_sample_days": 30,
                "regional_min_sample_size": 1,
                "real_projection_jitter_bps": 0,
            }
        },
        now=now,
        sample_seed=1,
    )

    assert projection is not None
    assert projection.troop_count == 250


@pytest.mark.django_db
def test_project_troops_preserves_total_across_multiple_types(settings, django_user_model, monkeypatch):
    from gameplay.services.virtual_players import _project_troops

    user = django_user_model.objects.create_user(username="projection_total_army", password="pass123")
    manor = ensure_manor(user)
    first = TroopTemplate.objects.create(key="projection_total_a", name="总兵力护院甲")
    second = TroopTemplate.objects.create(key="projection_total_b", name="总兵力护院乙")
    legacy = TroopTemplate.objects.create(key="projection_total_legacy", name="旧配置护院")
    PlayerTroop.objects.create(manor=manor, troop_template=legacy, count=50)
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {"troop_template_keys": [second.key, first.key]},
    }
    monkeypatch.setattr(connection.features, "supports_update_conflicts_with_target", False)

    _project_troops(manor, count=251, config=settings.VIRTUAL_PLAYER_CONFIG)
    _project_troops(manor, count=251, config=settings.VIRTUAL_PLAYER_CONFIG)

    troops = dict(manor.troops.filter(count__gt=0).values_list("troop_template__key", "count"))
    assert troops == {first.key: 126, second.key: 125}
    assert sum(troops.values()) == 251
    assert PlayerTroop.objects.get(manor=manor, troop_template=legacy).count == 0


def test_troop_projection_variation_is_stable_bounded_and_diverse():
    from gameplay.services.virtual_players import BotProjectionConfig, _apply_persona_to_projection

    projection = BotProjectionConfig(900, 3, 0, 3, troop_count=1000)
    config = {"combat_personas": {"balanced": {"troop_multiplier": 1.0}}}

    values = [
        _apply_persona_to_projection(
            projection,
            archetype="balanced",
            config=config,
            growth_seed=seed,
        ).troop_count
        for seed in range(20)
    ]

    assert (
        values[7]
        == _apply_persona_to_projection(
            projection,
            archetype="balanced",
            config=config,
            growth_seed=7,
        ).troop_count
    )
    assert all(900 <= value <= 1100 for value in values)
    assert len(set(values)) > 1


@pytest.mark.django_db
def test_target_band_growth_stage_is_initialized_and_capped(settings):
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2_000]},
        "growth": {"stage_caps": {"newbie": 3, "junior": 6}},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=42,
        now=now,
        projection=BotProjectionConfig(prestige=1_000, building_level=6, guest_count=0, guest_level=1),
    )

    assert profile.growth_stage == 6
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(seconds=1))
    assert maintain_due_virtual_players(now=now, limit=1) == 1

    profile.refresh_from_db()
    assert profile.growth_stage == 6


@pytest.mark.django_db
def test_slowing_growth_is_bounded_below_active_growth(settings, django_user_model):
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2_000]},
        "growth": {
            "stage_caps": {"junior": 10},
            "catch_up_ratio": 0.25,
            "slowing_ratio_multiplier": 0.5,
            "max_building_step": 3,
        },
        "projection": {
            "active_sample_days": 30,
            "regional_min_sample_size": 1,
            "real_projection_jitter_bps": 0,
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
        },
    }
    real_user = django_user_model.objects.create_user(username="bounded_growth_real", password="pass123")
    real_manor = ensure_manor(real_user)
    real_manor.region = "north"
    real_manor.prestige = 1_500
    real_manor.last_active_at = now
    real_manor.save(update_fields=["region", "prestige", "last_active_at"])
    for building in real_manor.buildings.all():
        building.level = 9
        building.save(update_fields=["level"])

    active = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=301,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    slowing = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=302,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    BotProfile.objects.filter(pk=active.pk).update(next_growth_at=now - timedelta(seconds=1))
    BotProfile.objects.filter(pk=slowing.pk).update(
        state=BotProfile.State.SLOWING,
        next_growth_at=now - timedelta(seconds=1),
    )

    assert maintain_due_virtual_players(now=now, limit=2) == 2

    active.refresh_from_db()
    slowing.refresh_from_db()
    assert active.growth_stage == 5
    assert slowing.growth_stage == 4


@pytest.mark.django_db
def test_abandoned_profile_never_resumes_combat_growth_when_dates_are_future(settings):
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2_000]},
        "growth": {"stage_caps": {"junior": 10}},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=303,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    before_levels = list(profile.manor.buildings.order_by("id").values_list("level", flat=True))
    BotProfile.objects.filter(pk=profile.pk).update(
        state=BotProfile.State.ABANDONED,
        next_growth_at=now - timedelta(seconds=1),
        abandon_at=now + timedelta(days=10),
        retire_at=now + timedelta(days=20),
    )

    assert maintain_due_virtual_players(now=now, limit=1) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ABANDONED
    assert profile.growth_stage == 3
    assert list(profile.manor.buildings.order_by("id").values_list("level", flat=True)) == before_levels


@pytest.mark.django_db
def test_abandoned_combat_persona_does_not_grow_while_lifecycle_state_is_active(settings):
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2_000]},
        "growth": {"stage_caps": {"junior": 10}},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.ABANDONED,
        growth_seed=304,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    before_levels = list(profile.manor.buildings.order_by("id").values_list("level", flat=True))
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(seconds=1))

    assert maintain_due_virtual_players(now=now, limit=1) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE
    assert profile.growth_stage == 3
    assert list(profile.manor.buildings.order_by("id").values_list("level", flat=True)) == before_levels


@pytest.mark.django_db
def test_lifecycle_persona_controls_profile_dates_independently_from_combat_archetype(settings):
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2_000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
        "lifecycle_personas": {
            "tourist": {"weight": 1, "active_days": [7, 7], "abandoned_days": [10, 10]},
            "casual": {"weight": 0, "active_days": [30, 30], "abandoned_days": [20, 20]},
            "committed": {"weight": 0, "active_days": [90, 90], "abandoned_days": [30, 30]},
            "veteran": {"weight": 0, "active_days": [180, 180], "abandoned_days": [60, 60]},
        },
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.GUARD,
        growth_seed=404,
        now=now,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )

    assert profile.abandon_at == now + timedelta(days=7)
    assert profile.retire_at == now + timedelta(days=17)


@pytest.mark.django_db
def test_virtual_player_guest_teams_are_diverse_across_same_configuration(settings):
    templates = [
        GuestTemplate.objects.create(
            key=f"virtual_diversity_guest_{index}",
            name=f"多样化门客{index}",
            archetype=GuestArchetype.MILITARY,
            rarity=GuestRarity.GREEN,
            base_attack=100 + index,
            base_defense=100 + index,
            base_hp=1_000 + index,
        )
        for index in range(4)
    ]
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2_000]},
        "projection": {
            "guest_template_keys": [template.key for template in templates],
            "gear_template_keys": [],
            "troop_template_keys": [],
        },
    }
    projection = BotProjectionConfig(prestige=800, building_level=5, guest_count=2, guest_level=5)

    first = create_virtual_player(region="north", prestige_band="junior", growth_seed=101, projection=projection)
    second = create_virtual_player(region="north", prestige_band="junior", growth_seed=202, projection=projection)

    first_team = list(first.manor.guests.values_list("template__key", flat=True))
    second_team = list(second.manor.guests.values_list("template__key", flat=True))
    assert len(set(first_team)) == len(first_team)
    assert len(set(second_team)) == len(second_team)
    assert len(set(first_team + second_team)) >= 3


@pytest.mark.django_db
def test_virtual_player_inventory_uses_a_small_persistent_archetype_pool(settings):
    templates = [
        ItemTemplate.objects.create(
            key=f"virtual_inventory_pool_{index}",
            name=f"库存物品{index}",
            effect_type=effect_type,
            tradeable=True,
            rarity="green",
            price=100,
        )
        for index, effect_type in enumerate(
            [
                ItemTemplate.EffectType.RESOURCE_PACK,
                ItemTemplate.EffectType.RESOURCE,
                ItemTemplate.EffectType.EXPERIENCE_ITEM,
                ItemTemplate.EffectType.MEDICINE,
            ]
        )
    ]
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2_000]},
        "growth": {"stage_caps": {"newbie": 3, "junior": 6}},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "item_template_keys": [template.key for template in templates],
            "inventory_template_slots_by_archetype": {"rich": 2},
            "inventory_effect_type_weights": {"rich": {"resource_pack": 10, "resource": 1}},
        },
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.RICH,
        growth_seed=303,
        now=now,
        projection=BotProjectionConfig(prestige=900, building_level=6, guest_count=0, guest_level=1),
    )
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(seconds=1))

    assert maintain_due_virtual_players(now=now, limit=1) == 1

    profile.refresh_from_db()
    stocked_keys = set(InventoryItem.objects.filter(manor=profile.manor).values_list("template__key", flat=True))
    assert len(profile.inventory_template_keys) == 2
    assert set(profile.inventory_template_keys) == stocked_keys

    first_pool = list(profile.inventory_template_keys)
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(seconds=1))
    assert maintain_due_virtual_players(now=now, limit=1) == 1

    profile.refresh_from_db()
    assert profile.inventory_template_keys == first_pool


@pytest.mark.django_db
def test_virtual_player_inventory_pool_removes_only_stale_owned_loot_candidates(settings):
    from gameplay.services.virtual_players import _replenish_inventory_stock

    pool_template = ItemTemplate.objects.create(
        key="virtual_pool_kept",
        name="池内物品",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        tradeable=True,
        rarity="green",
        price=100,
    )
    stale_template = ItemTemplate.objects.create(
        key="virtual_pool_stale",
        name="池外旧补货",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        tradeable=True,
        rarity="green",
        price=100,
    )
    nontradeable_template = ItemTemplate.objects.create(
        key="virtual_pool_nontradeable",
        name="不可交易资产",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        tradeable=False,
        rarity="green",
        price=0,
    )
    special_template = ItemTemplate.objects.create(
        key="virtual_pool_special",
        name="配置外特殊资产",
        effect_type=ItemTemplate.EffectType.TOOL,
        tradeable=True,
        rarity="purple",
        price=1000,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "item_template_keys": [
                pool_template.key,
                stale_template.key,
                nontradeable_template.key,
                special_template.key,
            ],
            "inventory_template_slots_by_archetype": {"balanced": 1},
            "loot_item_quantity": [1, 1],
        },
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=304,
        now=now,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    profile.inventory_template_keys = [pool_template.key]
    profile.save(update_fields=["inventory_template_keys", "updated_at"])
    for template in (pool_template, stale_template, nontradeable_template, special_template):
        InventoryItem.objects.create(
            manor=profile.manor,
            template=template,
            quantity=1,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )

    _replenish_inventory_stock(
        profile,
        profile.manor,
        level=3,
        rng=random.Random(304),
        config=settings.VIRTUAL_PLAYER_CONFIG,
        archetype=BotProfile.Archetype.BALANCED,
        growth_stage=3,
        prestige=900,
        now=now,
    )

    remaining_keys = set(
        InventoryItem.objects.filter(manor=profile.manor, quantity__gt=0).values_list("template__key", flat=True)
    )
    assert remaining_keys == {pool_template.key, nontradeable_template.key, special_template.key}


@pytest.mark.django_db
def test_virtual_guest_gear_uses_real_slot_capacities(settings):
    guest_template = GuestTemplate.objects.create(
        key="virtual_real_gear_slots_guest",
        name="装备槽位门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_attack=100,
        base_defense=100,
        base_hp=1_000,
    )
    gear_templates = []
    for slot in GearSlot:
        for index in range(3 if slot in {GearSlot.DEVICE, GearSlot.ORNAMENT} else 1):
            gear_templates.append(
                GearTemplate.objects.create(
                    key=f"virtual_real_gear_{slot.value}_{index}",
                    name=f"{slot.label}{index}",
                    slot=slot,
                    rarity=GuestRarity.GREEN,
                    attack_bonus=index + 1,
                )
            )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [guest_template.key],
            "gear_template_keys": [template.key for template in gear_templates],
            "gear_slots_by_archetype": {"balanced": 1},
            "gear_max_rarity_by_stage": {1: "green"},
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=501,
        projection=BotProjectionConfig(prestige=800, building_level=1, guest_count=1, guest_level=1),
    )
    guest = profile.manor.guests.get()
    equipped_by_slot = {slot: GearItem.objects.filter(guest=guest, template__slot=slot).count() for slot in GearSlot}

    assert equipped_by_slot == {
        GearSlot.HELMET: 1,
        GearSlot.ARMOR: 1,
        GearSlot.WEAPON: 1,
        GearSlot.SHOES: 1,
        GearSlot.DEVICE: 3,
        GearSlot.MOUNT: 1,
        GearSlot.ORNAMENT: 3,
    }


@pytest.mark.django_db
def test_virtual_guest_gear_replaces_weaker_template_after_stage_growth(settings):
    guest_template = GuestTemplate.objects.create(
        key="virtual_gear_upgrade_guest",
        name="装备成长门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_attack=100,
        base_defense=100,
        base_hp=1_000,
    )
    green_weapon = GearTemplate.objects.create(
        key="virtual_gear_upgrade_green",
        name="绿装武器",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        attack_bonus=10,
    )
    purple_weapon = GearTemplate.objects.create(
        key="virtual_gear_upgrade_purple",
        name="紫装武器",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.PURPLE,
        attack_bonus=50,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"middle": [2_000, 8_000]},
        "growth": {"stage_caps": {"middle": 10}},
        "projection": {
            "guest_template_keys": [guest_template.key],
            "gear_template_keys": [green_weapon.key, purple_weapon.key],
            "gear_max_rarity_by_stage": {1: "green", 7: "purple"},
        },
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="middle",
        growth_seed=502,
        now=now,
        projection=BotProjectionConfig(prestige=2_100, building_level=1, guest_count=1, guest_level=1),
    )
    guest = profile.manor.guests.get()
    assert GearItem.objects.get(guest=guest, template__slot=GearSlot.WEAPON).template_id == green_weapon.id

    BotProfile.objects.filter(pk=profile.pk).update(growth_stage=6, next_growth_at=now - timedelta(seconds=1))
    assert maintain_due_virtual_players(now=now, limit=1) == 1

    assert GearItem.objects.get(guest=guest, template__slot=GearSlot.WEAPON).template_id == purple_weapon.id


@pytest.mark.django_db
def test_virtual_guest_skills_follow_stage_targets_and_prefer_one_active_two_passive(settings):
    guest_template = GuestTemplate.objects.create(
        key="virtual_skill_growth_guest",
        name="技能成长门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_attack=100,
        base_defense=100,
        base_hp=1_000,
    )
    active = Skill.objects.create(
        key="virtual_skill_growth_active",
        name="主动技",
        kind=SkillKind.ACTIVE,
        required_level=1,
    )
    passives = [
        Skill.objects.create(
            key=f"virtual_skill_growth_passive_{index}",
            name=f"被动技{index}",
            kind=SkillKind.PASSIVE,
            required_level=1,
        )
        for index in range(3)
    ]
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"middle": [2_000, 8_000]},
        "growth": {"stage_caps": {"middle": 15}},
        "projection": {
            "guest_template_keys": [guest_template.key],
            "gear_template_keys": [],
            "extra_skill_keys": [active.key, *(skill.key for skill in passives)],
            "extra_skills_per_guest": [3, 3],
            "early_stage_skill_max": 6,
            "early_stage_skill_count": [0, 1],
            "multi_skill_passive_focus_chance": 1.0,
        },
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="middle",
        growth_seed=503,
        now=now,
        projection=BotProjectionConfig(prestige=2_100, building_level=1, guest_count=1, guest_level=1),
    )
    guest = profile.manor.guests.get()
    assert guest.guest_skills.count() <= 1

    BotProfile.objects.filter(pk=profile.pk).update(growth_stage=10, next_growth_at=now - timedelta(seconds=1))
    assert maintain_due_virtual_players(now=now, limit=1) == 1

    kinds = list(guest.guest_skills.order_by("skill__kind").values_list("skill__kind", flat=True))
    assert kinds.count(SkillKind.ACTIVE) == 1
    assert kinds.count(SkillKind.PASSIVE) == 2
