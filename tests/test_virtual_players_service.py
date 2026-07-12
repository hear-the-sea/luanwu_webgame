from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from itertools import count

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from battle.models import TroopTemplate
from common.constants.resources import ResourceType
from gameplay.constants import BuildingKeys, PVPConstants
from gameplay.models import (
    Building,
    BuildingType,
    InventoryItem,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
    RaidRun,
    ScoutRecord,
)
from gameplay.services.manor.core import calculate_building_capacity, ensure_manor
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestSkill,
    GuestStatus,
    GuestTemplate,
    Skill,
)

_COUNTER = count(1)

SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT = (
    "UNIQUE constraint failed: gameplay_manor.occupied_region, "
    "gameplay_manor.coordinate_x, gameplay_manor.coordinate_y"
)
MYSQL_OCCUPIED_MANOR_LOCATION_CONFLICT = (
    "Duplicate entry 'north-411-511' for key " "'gameplay_manor.unique_occupied_manor_location'"
)


def _unique(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


def _mysql_occupied_manor_location_conflict() -> IntegrityError:
    error = IntegrityError("Django wrapped database integrity error")
    error.__cause__ = Exception(1062, MYSQL_OCCUPIED_MANOR_LOCATION_CONFLICT)
    return error


def _create_building_type(key: str) -> None:
    BuildingType.objects.get_or_create(
        key=key,
        defaults={
            "name": key,
            "resource_type": ResourceType.SILVER,
            "base_cost": {},
        },
    )


def _bootstrap_projection_templates() -> dict[str, object]:
    for key in (
        BuildingKeys.SILVER_VAULT,
        BuildingKeys.GRANARY,
        BuildingKeys.JUXIAN_ZHUANG,
        BuildingKeys.JIADING_FANG,
        BuildingKeys.YOUXIA_BAOTA,
        BuildingKeys.LIANGGONG_CHANG,
    ):
        _create_building_type(key)

    skill = Skill.objects.create(
        key=_unique("bot_skill"),
        name="虚拟玩家测试技能",
        rarity=GuestRarity.GREEN,
        required_level=1,
    )
    guest_template = GuestTemplate.objects.create(
        key=_unique("bot_guest_tpl"),
        name="虚拟玩家测试门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_attack=180,
        base_defense=160,
        base_intellect=120,
        base_agility=100,
    )
    guest_template.initial_skills.add(skill)
    gear_template = GearTemplate.objects.create(
        key=_unique("bot_gear_tpl"),
        name="虚拟玩家测试装备",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
        attack_bonus=25,
    )
    troop_template = TroopTemplate.objects.create(
        key=_unique("bot_troop_tpl"),
        name="虚拟玩家测试护院",
        default_count=120,
    )
    return {"guest_template": guest_template, "gear_template": gear_template, "troop_template": troop_template}


@pytest.mark.django_db
def test_create_virtual_player_retries_coordinate_conflict_without_duplicate_side_effects(
    settings,
    monkeypatch,
    django_user_model,
):
    from gameplay.models import BotProfile
    from gameplay.services import virtual_players
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        }
    }

    initial_user_count = django_user_model.objects.count()
    initial_manor_count = Manor.objects.count()
    initial_profile_count = BotProfile.objects.count()
    coordinates = iter([(411, 511), (412, 512)])
    coordinate_calls = 0
    final_save_attempts = 0
    projection_calls = 0
    original_save = Manor.save
    original_project_buildings = virtual_players._project_buildings

    def _next_coordinate(_region):
        nonlocal coordinate_calls
        coordinate_calls += 1
        return next(coordinates)

    def _save_with_one_coordinate_conflict(self, *args, **kwargs):
        nonlocal final_save_attempts
        update_fields = kwargs.get("update_fields") or []
        if "coordinate_x" in update_fields and "last_active_at" in update_fields:
            final_save_attempts += 1
            if final_save_attempts == 1:
                raise _mysql_occupied_manor_location_conflict()
        return original_save(self, *args, **kwargs)

    def _count_project_buildings(*args, **kwargs):
        nonlocal projection_calls
        projection_calls += 1
        return original_project_buildings(*args, **kwargs)

    monkeypatch.setattr(virtual_players, "generate_unique_coordinate", _next_coordinate)
    monkeypatch.setattr(Manor, "save", _save_with_one_coordinate_conflict)
    monkeypatch.setattr(virtual_players, "_project_buildings", _count_project_buildings)

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=6001,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
    )

    profile.manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y"])
    assert (profile.manor.coordinate_x, profile.manor.coordinate_y) == (412, 512)
    assert coordinate_calls == 2
    assert final_save_attempts == 2
    assert projection_calls == 1
    assert django_user_model.objects.count() == initial_user_count + 1
    assert Manor.objects.count() == initial_manor_count + 1
    assert BotProfile.objects.count() == initial_profile_count + 1
    assert BotProfile.objects.filter(pk=profile.pk, manor=profile.manor).count() == 1


@pytest.mark.django_db
def test_create_virtual_player_rolls_back_after_coordinate_retry_exhaustion(
    settings,
    monkeypatch,
    django_user_model,
):
    from gameplay.models import BotProfile
    from gameplay.services import virtual_players
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        }
    }

    initial_user_count = django_user_model.objects.count()
    initial_manor_count = Manor.objects.count()
    initial_profile_count = BotProfile.objects.count()
    coordinate_values = count(610)
    coordinate_conflicts: list[IntegrityError] = []
    original_save = Manor.save

    def _next_coordinate(_region):
        value = next(coordinate_values)
        return value, value

    def _save_with_coordinate_conflict(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields") or []
        if "coordinate_x" in update_fields and "last_active_at" in update_fields:
            conflict = IntegrityError(SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT)
            coordinate_conflicts.append(conflict)
            raise conflict
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(virtual_players, "generate_unique_coordinate", _next_coordinate)
    monkeypatch.setattr(Manor, "save", _save_with_coordinate_conflict)

    with pytest.raises(IntegrityError) as exc_info:
        create_virtual_player(
            region="north",
            prestige_band="junior",
            growth_seed=6002,
            projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
        )

    assert len(coordinate_conflicts) == 5
    assert exc_info.value is coordinate_conflicts[-1]
    assert django_user_model.objects.count() == initial_user_count
    assert Manor.objects.count() == initial_manor_count
    assert BotProfile.objects.count() == initial_profile_count


@pytest.mark.parametrize(
    "error_message",
    [
        pytest.param(
            "NOT NULL constraint failed: gameplay_manor.region",
            id="not-null",
        ),
        pytest.param("FOREIGN KEY constraint failed", id="foreign-key"),
        pytest.param(
            "Duplicate entry 'taken-name' for key 'gameplay_manor.name'",
            id="other-unique",
        ),
        pytest.param(
            "Duplicate entry 'north-1-2' for key " "'gameplay_manor.unique_occupied_manor_location_shadow'",
            id="mysql-similar-unique",
        ),
        pytest.param(
            f"{SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT}, gameplay_manor.user_id",
            id="sqlite-four-column-unique",
        ),
    ],
)
@pytest.mark.django_db
def test_virtual_player_coordinate_retry_propagates_non_coordinate_integrity_error(
    error_message,
    monkeypatch,
    django_user_model,
):
    from gameplay.services import virtual_players

    manor = django_user_model.objects.create_user(
        username=_unique("bot_coordinate_non_target"),
        password="pass123",
    ).manor
    integrity_error = IntegrityError(error_message)
    save_attempts = 0
    coordinate_calls = 0

    def _raise_non_coordinate_error(*_args, **_kwargs):
        nonlocal save_attempts
        save_attempts += 1
        raise integrity_error

    def _track_coordinate_change(*_args, **_kwargs):
        nonlocal coordinate_calls
        coordinate_calls += 1

    monkeypatch.setattr(manor, "save", _raise_non_coordinate_error)
    monkeypatch.setattr(virtual_players, "_set_unique_location", _track_coordinate_change)

    with pytest.raises(IntegrityError) as exc_info:
        virtual_players._save_virtual_player_manor_with_coordinate_retry(
            manor,
            region="north",
            update_fields=["region", "coordinate_x", "coordinate_y"],
        )

    assert exc_info.value is integrity_error
    assert save_attempts == 1
    assert coordinate_calls == 0


def _create_real_manor_for_pvp(django_user_model, *, username: str, prestige: int = 500) -> Manor:
    user = django_user_model.objects.create_user(username=_unique(username), password="pass123")
    manor = ensure_manor(user)
    manor.region = "north"
    manor.coordinate_x = 20 + next(_COUNTER)
    manor.coordinate_y = 30 + next(_COUNTER)
    manor.prestige = prestige
    manor.newbie_protection_until = None
    manor.defeat_protection_until = None
    manor.peace_shield_until = None
    manor.last_active_at = timezone.now()
    manor.save(
        update_fields=[
            "region",
            "coordinate_x",
            "coordinate_y",
            "prestige",
            "newbie_protection_until",
            "defeat_protection_until",
            "peace_shield_until",
            "last_active_at",
        ]
    )
    return manor


def _run_due_bot_maintenance(profile, *, now, growth_stage: int = 1) -> None:
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import maintain_due_virtual_players

    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        growth_stage=growth_stage,
    )
    assert maintain_due_virtual_players(now=now, limit=10) >= 1
    profile.refresh_from_db()


@pytest.mark.django_db
def test_create_virtual_player_projects_real_manor_growth_data(settings, caplog):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [templates["gear_template"].key],
            "troop_template_keys": [templates["troop_template"].key],
            "technology_keys": ["dao_attack", "dao_defense"],
        }
    }

    now = timezone.now()
    caplog.set_level(logging.INFO, logger="gameplay.services.virtual_players")
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.DOJO,
        growth_seed=77,
        now=now,
        projection=BotProjectionConfig(prestige=1500, building_level=6, guest_count=2, guest_level=8),
    )

    manor = profile.manor
    assert profile.state == BotProfile.State.ACTIVE
    assert profile.next_growth_at > now
    assert profile.abandon_at > now
    assert profile.retire_at > profile.abandon_at
    assert manor.region == "north"
    assert manor.prestige == 1500
    assert manor.newbie_protection_until is None
    assert manor.user.has_usable_password() is False

    silver_vault = manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT)
    granary = manor.buildings.get(building_type__key=BuildingKeys.GRANARY)
    assert silver_vault.level == 6
    assert granary.level == 6
    assert manor.silver_capacity == calculate_building_capacity(6, is_silver_vault=True)
    assert manor.grain_capacity == calculate_building_capacity(6, is_silver_vault=False)
    assert manor.silver == 5000
    assert manor.grain == 1200

    assert PlayerTechnology.objects.filter(manor=manor, level__gt=0).count() == 0
    assert Guest.objects.filter(manor=manor, level=8).count() == 2
    assert GearItem.objects.filter(manor=manor, guest__manor=manor).count() == 2
    assert all(guest.skills.exists() for guest in manor.guests.all())
    assert manor.troops.filter(count__gt=0).exists()
    created_log = next(
        record for record in caplog.records if getattr(record, "event", None) == "virtual_player_created"
    )
    assert created_log.region == "north"
    assert created_log.prestige_band == "junior"
    assert profile.target_prestige_band == "junior"
    assert profile.current_prestige_band == "junior"
    assert created_log.archetype == BotProfile.Archetype.DOJO
    assert created_log.manor_id == manor.id


@pytest.mark.django_db
def test_create_virtual_player_projects_the_requested_high_prestige_band(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    expensive_loot = ItemTemplate.objects.create(
        key=_unique("bot_newbie_expensive"),
        name="新手不应初始持有的高价值物品",
        rarity="purple",
        tradeable=True,
        price=1_000_000,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [templates["gear_template"].key],
            "troop_template_keys": [templates["troop_template"].key],
            "item_template_keys": [expensive_loot.key],
            "loot_item_quantity": [3, 3],
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="senior",
        archetype=BotProfile.Archetype.RICH,
        growth_seed=777,
        projection=BotProjectionConfig(prestige=12_000, building_level=10, guest_count=5, guest_level=18),
    )

    manor = profile.manor
    assert manor.prestige == 12_000
    assert manor.silver == 5000
    assert manor.grain == 1200
    assert manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT).level == 10
    assert manor.guests.count() == 5
    assert all(guest.level == 18 for guest in manor.guests.all())
    assert InventoryItem.objects.filter(manor=manor, template=expensive_loot).count() == 0


@pytest.mark.django_db
def test_population_roll_samples_projection_from_real_players_in_same_region(settings, django_user_model):
    from gameplay.services.virtual_players import roll_virtual_player_population

    _bootstrap_projection_templates()
    real_user = django_user_model.objects.create_user(username=_unique("projection_real"), password="pass123")
    real_manor = ensure_manor(real_user)
    real_manor.region = "north"
    real_manor.prestige = 300
    real_manor.last_active_at = timezone.now()
    real_manor.save(update_fields=["region", "prestige", "last_active_at"])
    for building_type in BuildingType.objects.filter(key__in=(BuildingKeys.SILVER_VAULT, BuildingKeys.GRANARY)):
        Building.objects.update_or_create(
            manor=real_manor,
            building_type=building_type,
            defaults={"level": 12},
        )

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 1,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 5,
            "rolling_batch_size": [1, 1],
        },
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }

    assert roll_virtual_player_population(limit=1, now=timezone.now()) == 1

    bot_manor = Manor.objects.get(bot_profile__isnull=False)
    assert 0 <= bot_manor.prestige < 500
    assert bot_manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT).level == 1


@pytest.mark.django_db
def test_create_virtual_player_guests_learn_configured_extra_skills(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    extra_skill = Skill.objects.create(
        key=_unique("bot_extra_skill"),
        name="虚拟玩家额外技能",
        rarity=GuestRarity.BLUE,
        required_level=5,
    )
    blocked_skill = Skill.objects.create(
        key=_unique("bot_blocked_skill"),
        name="等级不足技能",
        rarity=GuestRarity.PURPLE,
        required_level=99,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "extra_skill_keys": [extra_skill.key, blocked_skill.key],
            "extra_skills_per_guest": [1, 1],
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.DOJO,
        growth_seed=271,
        projection=BotProjectionConfig(prestige=1200, building_level=6, guest_count=1, guest_level=8),
    )
    _run_due_bot_maintenance(profile, now=timezone.now(), growth_stage=7)

    guest = profile.manor.guests.get()
    learned = {row.skill.key: row.source for row in GuestSkill.objects.filter(guest=guest).select_related("skill")}
    assert learned[extra_skill.key] == GuestSkill.Source.BOOK
    assert blocked_skill.key not in learned


@pytest.mark.django_db
def test_create_virtual_player_guests_rarely_learn_configured_high_tier_skills(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    high_tier_skill = Skill.objects.create(
        key=_unique("bot_high_tier_skill"),
        name="虚拟玩家高阶技能",
        rarity=GuestRarity.PURPLE,
        required_level=8,
    )
    blocked_skill = Skill.objects.create(
        key=_unique("bot_blocked_high_tier_skill"),
        name="属性不足高阶技能",
        rarity=GuestRarity.PURPLE,
        required_level=99,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "extra_skill_keys": [],
            "high_tier_skill_keys": [high_tier_skill.key, blocked_skill.key],
            "high_tier_skill_chance": 1.0,
            "high_tier_skills_per_guest": [1, 1],
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.DOJO,
        growth_seed=272,
        projection=BotProjectionConfig(prestige=1200, building_level=6, guest_count=1, guest_level=8),
    )
    _run_due_bot_maintenance(profile, now=timezone.now(), growth_stage=7)

    guest = profile.manor.guests.get()
    learned = {row.skill.key: row.source for row in GuestSkill.objects.filter(guest=guest).select_related("skill")}
    assert learned[high_tier_skill.key] == GuestSkill.Source.BOOK
    assert blocked_skill.key not in learned


@pytest.mark.django_db
def test_configured_high_tier_skills_get_priority_over_regular_extra_skills(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    high_tier_skill = Skill.objects.create(
        key=_unique("bot_priority_high_tier_skill"),
        name="虚拟玩家优先高阶技能",
        rarity=GuestRarity.PURPLE,
        required_level=8,
    )
    regular_skills = [
        Skill.objects.create(
            key=_unique("bot_regular_extra_skill"),
            name=f"虚拟玩家普通技能{idx}",
            rarity=GuestRarity.BLUE,
            required_level=1,
        )
        for idx in range(2)
    ]
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "extra_skill_keys": [skill.key for skill in regular_skills],
            "extra_skills_per_guest": [2, 2],
            "high_tier_skill_keys": [high_tier_skill.key],
            "high_tier_skill_chance": 1.0,
            "high_tier_skills_per_guest": [1, 1],
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.DOJO,
        growth_seed=273,
        projection=BotProjectionConfig(prestige=1200, building_level=6, guest_count=1, guest_level=8),
    )
    _run_due_bot_maintenance(profile, now=timezone.now(), growth_stage=7)

    guest = profile.manor.guests.get()
    learned_keys = set(guest.guest_skills.values_list("skill__key", flat=True))
    assert high_tier_skill.key in learned_keys
    assert guest.guest_skills.count() == 3


@pytest.mark.django_db
def test_create_virtual_player_projects_tradeable_inventory_and_skips_untradeable(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_projection_templates()
    tradeable = ItemTemplate.objects.create(
        key=_unique("bot_loot_tradeable"),
        name="可掠夺投影物品",
        tradeable=True,
        storage_space=2,
    )
    untradeable = ItemTemplate.objects.create(
        key=_unique("bot_loot_bound"),
        name="绑定投影物品",
        tradeable=False,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
            "item_template_keys": [tradeable.key, untradeable.key],
            "loot_item_template_keys": [tradeable.key],
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.RICH,
        growth_seed=170,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
    )
    _run_due_bot_maintenance(profile, now=timezone.now(), growth_stage=3)

    projected_items = {
        item.template.key: item.quantity
        for item in InventoryItem.objects.filter(
            manor=profile.manor,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).select_related("template")
    }
    assert projected_items == {tradeable.key: 6}


@pytest.mark.django_db
def test_bot_archetype_changes_inventory_and_gear_projection(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    second_gear = GearTemplate.objects.create(
        key=_unique("bot_second_gear_tpl"),
        name="虚拟玩家第二件装备",
        slot=GearSlot.ARMOR,
        rarity=GuestRarity.GREEN,
        defense_bonus=20,
    )
    tradeable = ItemTemplate.objects.create(
        key=_unique("bot_archetype_loot"),
        name="类型差异库存",
        tradeable=True,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [templates["gear_template"].key, second_gear.key],
            "gear_slots_by_archetype": {"balanced": 1, "dojo": 2},
            "item_template_keys": [tradeable.key],
        }
    }

    balanced = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=301,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=1, guest_level=4),
    )
    rich = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.RICH,
        growth_seed=302,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=1, guest_level=4),
    )
    dojo = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.DOJO,
        growth_seed=303,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=1, guest_level=4),
    )
    now = timezone.now()
    _run_due_bot_maintenance(balanced, now=now, growth_stage=3)
    _run_due_bot_maintenance(rich, now=now, growth_stage=3)
    _run_due_bot_maintenance(dojo, now=now, growth_stage=3)

    balanced_qty = InventoryItem.objects.get(manor=balanced.manor, template=tradeable).quantity
    rich_qty = InventoryItem.objects.get(manor=rich.manor, template=tradeable).quantity
    assert rich_qty > balanced_qty
    assert GearItem.objects.filter(manor=balanced.manor, guest__isnull=False).count() == 1
    assert GearItem.objects.filter(manor=dojo.manor, guest__isnull=False).count() == 2


@pytest.mark.django_db
def test_create_virtual_player_materializes_configured_item_equipment_templates(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    item_template = ItemTemplate.objects.create(
        key=_unique("bot_item_equipment"),
        name="虚拟玩家配置装备",
        effect_type="equip_weapon",
        effect_payload={"force": 12},
        rarity=GuestRarity.GREEN,
        tradeable=True,
        price=800,
        storage_space=50,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [item_template.key],
            "gear_slots_by_archetype": {"balanced": 1},
        }
    }

    profile = create_virtual_player(
        region="west",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=9191,
        projection=BotProjectionConfig(prestige=200, building_level=3, guest_count=1, guest_level=6),
    )

    gear_template = GearTemplate.objects.get(key=item_template.key)
    equipped = GearItem.objects.get(manor=profile.manor, template=gear_template)
    assert equipped.guest is not None
    assert gear_template.extra_stats == {"force": 12}


@pytest.mark.django_db
def test_virtual_player_all_projection_pools_use_available_templates(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    GuestTemplate.objects.create(
        key=_unique("bot_all_guest_tpl"),
        name="虚拟玩家全量门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.BLUE,
        base_attack=160,
        base_defense=150,
        base_intellect=130,
        base_agility=110,
    )
    second_gear = GearTemplate.objects.create(
        key=_unique("bot_all_gear_tpl"),
        name="虚拟玩家全量装备",
        slot=GearSlot.ARMOR,
        rarity=GuestRarity.BLUE,
        defense_bonus=20,
    )
    second_troop = TroopTemplate.objects.create(
        key=_unique("bot_all_troop_tpl"),
        name="虚拟玩家全量护院",
        default_count=80,
    )
    tradeable = ItemTemplate.objects.create(
        key=_unique("bot_all_tradeable_item"),
        name="虚拟玩家全量可交易物品",
        rarity="green",
        tradeable=True,
        price=10,
        storage_space=1,
    )
    bound = ItemTemplate.objects.create(
        key=_unique("bot_all_bound_item"),
        name="虚拟玩家全量绑定物品",
        rarity="green",
        tradeable=False,
        price=10,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": "__all__",
            "gear_template_keys": "__all__",
            "gear_slots_by_archetype": {"balanced": 2},
            "troop_template_keys": "__all__",
            "technology_keys": "__all__",
            "item_template_keys": "__all_tradeable__",
            "loot_item_quantity": [1, 1],
            "inventory_quantity_multipliers": {"balanced": 1},
        }
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=8181,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=2, guest_level=4),
    )
    _run_due_bot_maintenance(profile, now=timezone.now(), growth_stage=3)

    guest_keys = set(profile.manor.guests.values_list("template__key", flat=True))
    assert len(guest_keys) == 2
    assert "__all__" not in guest_keys
    assert guest_keys <= set(GuestTemplate.objects.values_list("key", flat=True))
    gear_keys = set(GearItem.objects.filter(manor=profile.manor).values_list("template__key", flat=True))
    assert second_gear.key in gear_keys
    assert "__all__" not in gear_keys
    assert gear_keys <= set(GearTemplate.objects.values_list("key", flat=True))
    troop_keys = set(profile.manor.troops.values_list("troop_template__key", flat=True))
    assert {templates["troop_template"].key, second_troop.key} <= troop_keys
    tech_keys = set(PlayerTechnology.objects.filter(manor=profile.manor).values_list("tech_key", flat=True))
    assert {"dao_attack", "gong_recruit"} <= tech_keys
    item_keys = set(InventoryItem.objects.filter(manor=profile.manor).values_list("template__key", flat=True))
    assert tradeable.key in item_keys
    assert bound.key not in item_keys


@pytest.mark.django_db
def test_create_virtual_player_backfills_historical_timestamps(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [templates["gear_template"].key],
            "troop_template_keys": [],
            "technology_keys": [],
        }
    }
    now = timezone.now()

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.DOJO,
        growth_seed=207,
        now=now,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=1, guest_level=5),
    )
    manor = profile.manor
    guest = manor.guests.get()
    gears = list(GearItem.objects.filter(manor=manor))
    building = manor.buildings.first()

    assert manor.user.date_joined < now - timedelta(days=1)
    assert manor.created_at < now - timedelta(days=1)
    assert building is not None
    assert building.created_at < now - timedelta(days=1)
    assert guest.created_at < now - timedelta(days=1)
    assert gears
    assert all(gear.acquired_at < now - timedelta(days=1) for gear in gears)
    assert manor.created_at <= manor.last_active_at <= now


@pytest.mark.django_db
def test_virtual_players_are_searchable_but_excluded_from_real_rankings(settings, django_user_model):
    from gameplay.models import BotProfile
    from gameplay.services.raid.map_search import search_manors_by_region
    from gameplay.services.ranking import get_prestige_ranking, get_ranking_with_player_context
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {"projection": {"guest_template_keys": [], "gear_template_keys": []}}

    real_user = django_user_model.objects.create_user(username=_unique("real_rank_user"), password="pass123")
    real_manor = ensure_manor(real_user)
    real_manor.name = _unique("真实庄园")
    real_manor.region = "north"
    real_manor.coordinate_x = 10
    real_manor.coordinate_y = 10
    real_manor.prestige = 500
    real_manor.save(update_fields=["name", "region", "coordinate_x", "coordinate_y", "prestige"])

    bot_profile = create_virtual_player(
        region="north",
        prestige_band="senior",
        archetype=BotProfile.Archetype.RICH,
        growth_seed=88,
        now=timezone.now() - timedelta(days=5),
        projection=BotProjectionConfig(prestige=9000, building_level=3, guest_count=0, guest_level=1),
    )

    search_rows, total = search_manors_by_region(real_manor, "north", page=1, page_size=20)
    assert total >= 2
    assert any(row["id"] == bot_profile.manor_id for row in search_rows)

    ranking_ids = {row["manor_id"] for row in get_prestige_ranking(limit=10)}
    assert bot_profile.manor_id not in ranking_ids
    ranking_context = get_ranking_with_player_context(real_manor, limit=10)
    assert ranking_context["total_players"] == 1
    assert ranking_context["player_rank"] == 1


@pytest.mark.django_db(transaction=True)
def test_real_player_can_scout_virtual_player_and_receive_normal_intel(settings, django_user_model, monkeypatch):
    from gameplay.models import BotProfile
    from gameplay.services.raid import scout as scout_service
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "troop_template_keys": [templates["troop_template"].key],
        }
    }
    attacker = _create_real_manor_for_pvp(django_user_model, username="bot_scout_attacker", prestige=500)
    scout_template, _ = TroopTemplate.objects.get_or_create(
        key=PVPConstants.SCOUT_TROOP_KEY,
        defaults={"name": "探子"},
    )
    PlayerTroop.objects.update_or_create(manor=attacker, troop_template=scout_template, defaults={"count": 2})
    bot_profile = create_virtual_player(
        region="north",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.GUARD,
        growth_seed=8891,
        projection=BotProjectionConfig(prestige=450, building_level=3, guest_count=1, guest_level=3),
    )
    bot_profile.manor.coordinate_x = 25
    bot_profile.manor.coordinate_y = 35
    bot_profile.manor.save(update_fields=["coordinate_x", "coordinate_y"])

    monkeypatch.setattr(scout_service.scout_followups, "schedule_scout_completion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        scout_service.scout_followups, "schedule_scout_return_completion", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(scout_service, "_roll_scout_success", lambda: 0.0)

    record = scout_service.start_scout(attacker, bot_profile.manor)
    scout_service.finalize_scout(record, now=timezone.now())

    record.refresh_from_db()
    troop = PlayerTroop.objects.get(manor=attacker, troop_template=scout_template)
    assert troop.count == 1
    assert record.defender_id == bot_profile.manor_id
    assert record.status == ScoutRecord.Status.RETURNING
    assert record.is_success is True
    assert record.intel_data["guest_count"] == 1
    assert "troop_description" in record.intel_data
    assert "asset_level" in record.intel_data
    assert "bot" not in bot_profile.manor.display_name.lower()


@pytest.mark.django_db(transaction=True)
def test_real_player_can_start_raid_against_virtual_player(settings, django_user_model, monkeypatch):
    from gameplay.models import BotProfile
    from gameplay.services.raid.combat import runs as combat_runs
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    templates = _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "troop_template_keys": [templates["troop_template"].key],
        }
    }
    attacker = _create_real_manor_for_pvp(django_user_model, username="bot_raid_attacker", prestige=500)
    attacking_guest = Guest.objects.create(
        manor=attacker,
        template=templates["guest_template"],
        level=5,
        status=GuestStatus.IDLE,
    )
    bot_profile = create_virtual_player(
        region="north",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.GUARD,
        growth_seed=8892,
        projection=BotProjectionConfig(prestige=450, building_level=3, guest_count=1, guest_level=3),
    )

    monkeypatch.setattr(combat_runs, "_send_raid_incoming_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "_dispatch_raid_battle_task", lambda *_args, **_kwargs: None)

    run = combat_runs.start_raid(attacker, bot_profile.manor, [attacking_guest.id], {})

    attacking_guest.refresh_from_db()
    attacker.refresh_from_db()
    assert run.defender_id == bot_profile.manor_id
    assert run.status == RaidRun.Status.MARCHING
    assert run.guests.get().id == attacking_guest.id
    assert attacking_guest.status == GuestStatus.DEPLOYED
    assert attacker.action_points == 990


@pytest.mark.django_db
def test_due_virtual_player_maintenance_grows_or_marks_stale(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    templates = _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "troop_template_keys": [templates["troop_template"].key],
            "technology_keys": ["dao_attack"],
        }
    }

    now = timezone.now()
    growing = create_virtual_player(
        region="east",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=99,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=900, building_level=3, guest_count=1, guest_level=4),
    )
    retiring = create_virtual_player(
        region="east",
        prestige_band="junior",
        archetype=BotProfile.Archetype.ABANDONED,
        growth_seed=100,
        now=now - timedelta(days=100),
        projection=BotProjectionConfig(prestige=800, building_level=2, guest_count=1, guest_level=3),
    )

    BotProfile.objects.filter(pk=growing.pk).update(next_growth_at=now - timedelta(minutes=1))
    BotProfile.objects.filter(pk=retiring.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        abandon_at=now - timedelta(days=3),
        retire_at=now - timedelta(minutes=1),
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 2

    growing.refresh_from_db()
    retiring.refresh_from_db()
    assert growing.state == BotProfile.State.ACTIVE
    assert growing.growth_stage == 2
    assert growing.next_growth_at > now
    assert growing.manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT).level == 2
    assert PlayerTechnology.objects.get(manor=growing.manor, tech_key="dao_attack").level == 1

    assert retiring.state == BotProfile.State.STALE
    assert retiring.maintenance_stopped_at is None


@pytest.mark.django_db
def test_due_virtual_player_maintenance_samples_real_player_projection(settings, django_user_model):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    templates = _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {
            "guest_template_keys": [templates["guest_template"].key],
            "gear_template_keys": [],
            "troop_template_keys": [templates["troop_template"].key],
            "technology_keys": ["dao_attack"],
        },
    }
    now = timezone.now()
    real_user = django_user_model.objects.create_user(username=_unique("maintenance_real"), password="pass123")
    real_manor = ensure_manor(real_user)
    real_manor.region = "east"
    real_manor.prestige = 1500
    real_manor.save(update_fields=["region", "prestige"])
    for building_type in BuildingType.objects.filter(key__in=(BuildingKeys.SILVER_VAULT, BuildingKeys.GRANARY)):
        Building.objects.update_or_create(
            manor=real_manor,
            building_type=building_type,
            defaults={"level": 9},
        )
    Guest.objects.create(manor=real_manor, template=templates["guest_template"], level=11)
    Guest.objects.create(manor=real_manor, template=templates["guest_template"], level=12)

    profile = create_virtual_player(
        region="east",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=909,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=900, building_level=3, guest_count=1, guest_level=4),
    )
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1))

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.growth_stage == 2
    assert profile.manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT).level == 2
    assert profile.manor.guests.count() == 2
    assert set(profile.manor.guests.values_list("level", flat=True)) == {2, 4}
    assert PlayerTechnology.objects.get(manor=profile.manor, tech_key="dao_attack").level == 1


@pytest.mark.django_db
def test_virtual_player_rare_and_powerful_inventory_respects_daily_global_caps(settings, caplog):
    from gameplay.models import BotInventoryDailyCounter, BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    cache.clear()
    _bootstrap_projection_templates()
    common = ItemTemplate.objects.create(
        key=_unique("bot_cap_common"),
        name="普通库存",
        rarity="green",
        tradeable=True,
        price=10,
        storage_space=1,
    )
    rare = ItemTemplate.objects.create(
        key=_unique("bot_cap_rare"),
        name="稀有库存",
        rarity="purple",
        tradeable=True,
        price=10,
        storage_space=1,
    )
    powerful = ItemTemplate.objects.create(
        key=_unique("bot_cap_powerful"),
        name="高价值库存",
        rarity="green",
        tradeable=True,
        price=1_000_000,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [common.key, rare.key, powerful.key],
            "loot_item_quantity": [2, 2],
            "inventory_quantity_multipliers": {"balanced": 1},
            "rare_item_daily_global_cap": 1,
            "powerful_item_daily_global_cap": 1,
            "powerful_item_min_price": 100_000,
            "powerful_item_min_growth_stage": 3,
            "low_stage_powerful_item_chance": 1.0,
            "powerful_item_prestige_chance": [{"min_prestige": 0, "chance": 1.0}],
        }
    }
    now = timezone.now()
    caplog.set_level(logging.INFO, logger="gameplay.services.virtual_players")

    first = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=1710,
        now=now,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
    )
    second = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=1711,
        now=now,
        projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
    )
    _run_due_bot_maintenance(first, now=now, growth_stage=3)
    _run_due_bot_maintenance(second, now=now, growth_stage=3)

    assert InventoryItem.objects.filter(manor=first.manor, template=common).get().quantity == 2
    assert InventoryItem.objects.filter(manor=second.manor, template=common).get().quantity == 2
    assert InventoryItem.objects.filter(template=rare).aggregate(total=Sum("quantity"))["total"] == 1
    assert InventoryItem.objects.filter(template=powerful).aggregate(total=Sum("quantity"))["total"] == 1
    counter_date = timezone.localtime(now).date()
    assert BotInventoryDailyCounter.objects.get(category="rare", counter_date=counter_date).quantity == 1
    assert BotInventoryDailyCounter.objects.get(category="powerful", counter_date=counter_date).quantity == 1
    cap_logs = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "virtual_player_inventory_cap_truncated"
    ]
    assert any(
        record.category == "rare"
        and record.requested == 2
        and record.allowed == 1
        and record.cap == 1
        and record.date == counter_date.isoformat()
        for record in cap_logs
    )
    assert any(record.category == "powerful" and record.allowed < record.requested for record in cap_logs)


@pytest.mark.django_db
def test_virtual_player_inventory_cap_counter_rolls_back_with_failed_generation(settings):
    from gameplay.models import BotInventoryDailyCounter, BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    cache.clear()
    _bootstrap_projection_templates()
    rare = ItemTemplate.objects.create(
        key=_unique("bot_cap_rollback_rare"),
        name="回滚稀有库存",
        rarity="purple",
        tradeable=True,
        price=10,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [rare.key],
            "loot_item_quantity": [1, 1],
            "rare_item_daily_global_cap": 1,
        }
    }
    now = timezone.now()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            profile = create_virtual_player(
                region="north",
                prestige_band="junior",
                archetype=BotProfile.Archetype.BALANCED,
                growth_seed=1810,
                now=now,
                projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
            )
            BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1), growth_stage=3)
            maintain_due_virtual_players(now=now, limit=10)
            raise RuntimeError("rollback generation")

    assert BotInventoryDailyCounter.objects.count() == 0
    assert InventoryItem.objects.filter(template=rare).count() == 0


@pytest.mark.django_db(transaction=True)
def test_virtual_player_inventory_daily_counter_caps_repeated_creates_in_one_transaction(settings):
    from gameplay.models import BotInventoryDailyCounter, BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    cache.clear()
    _bootstrap_projection_templates()
    rare = ItemTemplate.objects.create(
        key=_unique("bot_cap_txn_rare"),
        name="事务稀有库存",
        rarity="purple",
        tradeable=True,
        price=10,
        storage_space=1,
    )
    powerful = ItemTemplate.objects.create(
        key=_unique("bot_cap_txn_powerful"),
        name="事务高价值库存",
        rarity="green",
        tradeable=True,
        price=1_000_000,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [rare.key, powerful.key],
            "loot_item_quantity": [1, 1],
            "inventory_quantity_multipliers": {"balanced": 1},
            "rare_item_daily_global_cap": 2,
            "powerful_item_daily_global_cap": 2,
            "powerful_item_min_price": 100_000,
            "powerful_item_min_growth_stage": 3,
            "low_stage_powerful_item_chance": 1.0,
            "powerful_item_prestige_chance": [{"min_prestige": 0, "chance": 1.0}],
        }
    }
    now = timezone.now()

    with transaction.atomic():
        profile_ids = []
        for offset in range(5):
            profile = create_virtual_player(
                region="north",
                prestige_band="junior",
                archetype=BotProfile.Archetype.BALANCED,
                growth_seed=1910 + offset,
                now=now,
                projection=BotProjectionConfig(prestige=1200, building_level=4, guest_count=0, guest_level=1),
            )
            profile_ids.append(profile.pk)
        BotProfile.objects.filter(pk__in=profile_ids).update(
            next_growth_at=now - timedelta(minutes=1),
            growth_stage=3,
        )
        maintain_due_virtual_players(now=now, limit=10)

    counter_date = timezone.localtime(now).date()
    assert BotInventoryDailyCounter.objects.get(category="rare", counter_date=counter_date).quantity == 2
    assert BotInventoryDailyCounter.objects.get(category="powerful", counter_date=counter_date).quantity == 2
    assert InventoryItem.objects.filter(template=rare).aggregate(total=Sum("quantity"))["total"] == 2
    assert InventoryItem.objects.filter(template=powerful).aggregate(total=Sum("quantity"))["total"] == 2
    assert InventoryItem.objects.filter(template__in=[rare, powerful]).count() == 4


@pytest.mark.django_db
def test_due_virtual_player_maintenance_replenishes_inventory_by_growth_stage(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    common = ItemTemplate.objects.create(
        key=_unique("bot_stage_common"),
        name="阶段普通补给",
        rarity="green",
        tradeable=True,
        price=100,
        storage_space=1,
    )
    valuable = ItemTemplate.objects.create(
        key=_unique("bot_stage_valuable"),
        name="阶段高价值补给",
        rarity="green",
        tradeable=True,
        price=1_000_000,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [common.key, valuable.key],
            "loot_item_quantity": [2, 2],
            "inventory_quantity_multipliers": {"balanced": 1},
            "low_stage_powerful_item_chance": 1.0,
            "powerful_item_min_growth_stage": 5,
            "powerful_item_daily_global_cap": 10,
            "powerful_item_min_price": 100_000,
            "powerful_item_prestige_chance": [{"min_prestige": 0, "chance": 1.0}],
        }
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=5150,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=1900, building_level=3, guest_count=0, guest_level=1),
    )
    InventoryItem.objects.filter(manor=profile.manor).delete()
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1), growth_stage=1)

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    stock = {
        item.template.key: item.quantity
        for item in InventoryItem.objects.filter(manor=profile.manor).select_related("template")
    }
    assert stock[common.key] == 2
    assert valuable.key not in stock

    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1), growth_stage=5)
    assert maintain_due_virtual_players(now=now, limit=10) == 1

    stock = {
        item.template.key: item.quantity
        for item in InventoryItem.objects.filter(manor=profile.manor).select_related("template")
    }
    assert stock[valuable.key] == 2


@pytest.mark.django_db
def test_high_value_inventory_projection_scales_by_bot_prestige(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    common = ItemTemplate.objects.create(
        key=_unique("bot_prestige_common"),
        name="声望普通补给",
        rarity="green",
        tradeable=True,
        price=100,
        storage_space=1,
    )
    powerful = ItemTemplate.objects.create(
        key=_unique("bot_prestige_powerful"),
        name="声望高价值补给",
        rarity="purple",
        tradeable=True,
        price=1_000_000,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [common.key, powerful.key],
            "loot_item_quantity": [1, 1],
            "inventory_quantity_multipliers": {"balanced": 1},
            "powerful_item_min_growth_stage": 5,
            "powerful_item_min_price": 100_000,
            "rare_item_daily_global_cap": 10,
            "powerful_item_daily_global_cap": 10,
            "low_stage_powerful_item_chance": 1.0,
            "powerful_item_prestige_chance": [
                {"min_prestige": 0, "chance": 0.0},
                {"min_prestige": 30000, "chance": 1.0},
            ],
        }
    }
    now = timezone.now()
    low = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=6160,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=1500, building_level=8, guest_count=0, guest_level=1),
    )
    high = create_virtual_player(
        region="north",
        prestige_band="veteran",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=6161,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=40000, building_level=8, guest_count=0, guest_level=1),
    )
    low.manor.prestige = 1500
    low.manor.save(update_fields=["prestige"])
    high.manor.prestige = 40000
    high.manor.save(update_fields=["prestige"])
    InventoryItem.objects.filter(manor__in=[low.manor, high.manor]).delete()
    BotProfile.objects.filter(pk__in=[low.pk, high.pk]).update(
        next_growth_at=now - timedelta(minutes=1),
        growth_stage=5,
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 2

    low_stock = set(InventoryItem.objects.filter(manor=low.manor).values_list("template__key", flat=True))
    high_stock = set(InventoryItem.objects.filter(manor=high.manor).values_list("template__key", flat=True))
    assert common.key in low_stock
    assert powerful.key not in low_stock
    assert common.key in high_stock
    assert powerful.key in high_stock


@pytest.mark.django_db
def test_invalid_prestige_chance_config_does_not_open_high_value_inventory(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    powerful = ItemTemplate.objects.create(
        key=_unique("bot_invalid_prestige_powerful"),
        name="异常配置高价值补给",
        rarity="purple",
        tradeable=True,
        price=1_000_000,
        storage_space=1,
    )
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [powerful.key],
            "loot_item_quantity": [1, 1],
            "powerful_item_min_growth_stage": 5,
            "powerful_item_min_price": 100_000,
            "rare_item_daily_global_cap": 10,
            "powerful_item_daily_global_cap": 10,
            "powerful_item_prestige_chance": "bad",
        }
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="veteran",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=6170,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=40000, building_level=8, guest_count=0, guest_level=1),
    )
    profile.manor.prestige = 40000
    profile.manor.save(update_fields=["prestige"])
    InventoryItem.objects.filter(manor=profile.manor).delete()
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        growth_stage=5,
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    assert not InventoryItem.objects.filter(manor=profile.manor, template=powerful).exists()


@pytest.mark.django_db
def test_limited_rare_inventory_projection_samples_across_full_item_pool(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    rare_items = [
        ItemTemplate.objects.create(
            key=_unique("bot_full_pool_rare"),
            name=f"全量池稀有补给{idx}",
            rarity="purple",
            tradeable=True,
            price=100,
            storage_space=1,
        )
        for idx in range(6)
    ]
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "item_template_keys": [item.key for item in rare_items],
            "loot_item_quantity": [1, 1],
            "inventory_quantity_multipliers": {"balanced": 1},
            "rare_item_daily_global_cap": 2,
            "powerful_item_min_growth_stage": 5,
            "powerful_item_prestige_chance": [{"min_prestige": 0, "chance": 1.0}],
        }
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=7000,
        now=now - timedelta(days=1),
        projection=BotProjectionConfig(prestige=1500, building_level=8, guest_count=0, guest_level=1),
    )
    InventoryItem.objects.filter(manor=profile.manor).delete()
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        growth_stage=5,
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    stocked_keys = list(
        InventoryItem.objects.filter(manor=profile.manor)
        .order_by("template__key")
        .values_list("template__key", flat=True)
    )
    expected_keys = [item.key for item in rare_items]
    seeded_order = sorted(expected_keys)
    from random import Random

    seeded_rng = Random(7005)
    seeded_rng.uniform(0.25, 0.55)
    seeded_rng.shuffle(seeded_order)
    assert len(stocked_keys) == 2
    assert stocked_keys == sorted(seeded_order[:2])


def test_inventory_daily_cap_reservation_recovers_from_counter_create_race(monkeypatch):
    from gameplay.services import virtual_players

    class Counter:
        quantity = 1

        def save(self, **kwargs):
            self.saved_kwargs = kwargs

    class LockedCounterManager:
        def __init__(self):
            self.counter = Counter()
            self.get_or_create_calls = 0
            self.get_calls = 0

        def get_or_create(self, **kwargs):
            self.get_or_create_calls += 1
            raise IntegrityError("duplicate counter")

        def get(self, **kwargs):
            self.get_calls += 1
            return self.counter

    class CounterManager:
        def __init__(self):
            self.locked = LockedCounterManager()

        def select_for_update(self):
            return self.locked

    class CounterModel:
        objects = CounterManager()

    monkeypatch.setattr(virtual_players, "BotInventoryDailyCounter", CounterModel)

    allowed = virtual_players._reserve_inventory_daily_cap(
        category="rare",
        requested=2,
        cap=3,
        now=timezone.now(),
    )

    assert allowed == 2
    assert CounterModel.objects.locked.counter.quantity == 3
    assert CounterModel.objects.locked.get_or_create_calls == 1
    assert CounterModel.objects.locked.get_calls == 1


@pytest.mark.django_db
def test_virtual_player_names_stay_unique_for_reused_growth_seed(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {"projection": {"guest_template_keys": [], "gear_template_keys": []}}

    first = create_virtual_player(
        region="south",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=12345,
        projection=BotProjectionConfig(prestige=100, building_level=1, guest_count=0, guest_level=1),
    )
    second = create_virtual_player(
        region="south",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=12345,
        projection=BotProjectionConfig(prestige=100, building_level=1, guest_count=0, guest_level=1),
    )

    assert first.manor.name != second.manor.name
    for manor_name in (first.manor.name, second.manor.name):
        assert "行旅" not in manor_name
        assert "bot" not in manor_name.lower()
        assert not any(ch.isdigit() for ch in manor_name)


@pytest.mark.django_db
def test_virtual_player_name_generator_can_emit_internet_style_names():
    from gameplay.services import virtual_players

    expected = {
        "坤哥亡命天涯",
        "听到涛声",
        "暴打派大星",
        "摸鱼山庄",
        "咸鱼小筑",
    }
    generated = {virtual_players._generate_bot_manor_name(growth_seed=seed) for seed in range(1, 80)}

    assert generated & expected


@pytest.mark.django_db
def test_virtual_player_name_generator_prefers_internet_style_names():
    from gameplay.services import virtual_players

    internet_prefixes = {
        "摸鱼",
        "开摆",
        "咸鱼",
        "随缘",
        "夜猫子",
        "奶茶续命",
        "快乐老家",
        "人间清醒",
        "低调发财",
        "菜但爱玩",
        "非酋",
        "欧皇",
        "一键收菜",
        "余额不足",
    }
    internet_standalone = {
        "坤哥亡命天涯",
        "听到涛声",
        "暴打派大星",
        "今天也想躺平",
        "打不过就跑",
        "路过不要打我",
        "先苟住再说",
        "上号收个菜",
        "差点就赢了",
        "全靠同行衬托",
        "别看我会输",
        "不想加班",
        "精神状态良好",
        "好运加载中",
        "这把随缘",
        "风紧扯呼",
    }

    def is_internet_style(name: str) -> bool:
        return name in internet_standalone or any(name.startswith(prefix) for prefix in internet_prefixes)

    generated = [virtual_players._generate_bot_manor_name(growth_seed=seed) for seed in range(1, 201)]
    internet_count = sum(1 for name in generated if is_internet_style(name))

    assert internet_count >= 120
    assert internet_count < len(generated)


@pytest.mark.django_db
def test_population_plan_and_roll_use_active_player_multiplier(settings, django_user_model):
    from gameplay.services.virtual_players import plan_virtual_player_population, roll_virtual_player_population

    _bootstrap_projection_templates()
    for idx in range(3):
        user = django_user_model.objects.create_user(username=_unique(f"active_real_{idx}"), password="pass123")
        manor = ensure_manor(user)
        manor.region = "north"
        manor.last_active_at = timezone.now()
        manor.save(update_fields=["region", "last_active_at"])

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 4,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 20,
            "rolling_batch_size": [20, 20],
        },
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }

    plan = plan_virtual_player_population(now=timezone.now())
    assert plan["active_real_players"] == 3
    assert plan["target_bot_total"] == 12
    assert roll_virtual_player_population(limit=20, now=timezone.now()) == 12


@pytest.mark.django_db
def test_population_roll_spreads_global_deficit_across_regions_and_bands(settings, django_user_model):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import roll_virtual_player_population

    _bootstrap_projection_templates()
    user = django_user_model.objects.create_user(username=_unique("active_real_for_spread"), password="pass123")
    manor = ensure_manor(user)
    manor.region = "north"
    manor.last_active_at = timezone.now()
    manor.save(update_fields=["region", "last_active_at"])

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 8,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 20,
            "rolling_batch_size": [8, 8],
        },
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }

    assert roll_virtual_player_population(limit=8, now=timezone.now()) == 8

    profiles = BotProfile.objects.select_related("manor").order_by("id")
    by_region = {}
    by_band = {}
    for profile in profiles:
        by_region[profile.manor.region] = by_region.get(profile.manor.region, 0) + 1
        by_band[profile.target_prestige_band] = by_band.get(profile.target_prestige_band, 0) + 1
    assert by_region == {"north": 2, "east": 2, "west": 2, "south": 2}
    assert len(by_band) > 1
    assert max(by_band.values()) - min(by_band.values()) <= 1


@pytest.mark.django_db
def test_population_roll_counts_actual_current_prestige_band(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 1,
            "hard_cap": 20,
            "rolling_batch_size": [20, 20],
        },
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    create_virtual_player(
        region="north",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=23458,
        now=now,
        projection=BotProjectionConfig(prestige=1900, building_level=3, guest_count=0, guest_level=1),
    )

    assert BotProfile.objects.get(target_prestige_band="junior").current_prestige_band == "junior"
    assert roll_virtual_player_population(limit=20, now=now) == 19
    assert BotProfile.objects.filter(target_prestige_band="junior", manor__region="north").count() == 1


@pytest.mark.django_db
def test_population_roll_continues_after_one_coordinate_conflict(settings, monkeypatch, django_user_model):
    from gameplay.models import BotProfile
    from gameplay.services import virtual_players
    from gameplay.services.virtual_players import roll_virtual_player_population

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 1,
            "min_attackable_per_band": 0,
            "hard_cap": 20,
            "rolling_batch_size": [2, 2],
        },
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        },
    }

    initial_user_count = django_user_model.objects.count()
    initial_manor_count = Manor.objects.count()
    initial_profile_count = BotProfile.objects.count()
    coordinate_values = count(710)
    final_save_attempts = 0
    original_save = Manor.save

    def _next_coordinate(_region):
        value = next(coordinate_values)
        return value, value + 1

    def _save_with_one_coordinate_conflict(self, *args, **kwargs):
        nonlocal final_save_attempts
        update_fields = kwargs.get("update_fields") or []
        if "coordinate_x" in update_fields and "last_active_at" in update_fields:
            final_save_attempts += 1
            if final_save_attempts == 1:
                raise IntegrityError(SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(virtual_players, "generate_unique_coordinate", _next_coordinate)
    monkeypatch.setattr(Manor, "save", _save_with_one_coordinate_conflict)

    assert roll_virtual_player_population(limit=2, now=timezone.now()) == 2
    assert final_save_attempts == 3
    assert django_user_model.objects.count() == initial_user_count + 2
    assert Manor.objects.count() == initial_manor_count + 2
    assert BotProfile.objects.count() == initial_profile_count + 2


def test_population_roll_release_does_not_delete_reacquired_owner_lock(monkeypatch):
    from core.utils import cache_lock
    from gameplay.services import virtual_players

    class _FakeCache:
        def __init__(self):
            self.values: dict[str, str] = {}
            self.deleted: list[str] = []

        def add(self, key, value, timeout=None):
            del timeout
            if key in self.values:
                return False
            self.values[key] = value
            return True

        def get(self, key, default=None):
            return self.values.get(key, default)

        def delete(self, key):
            self.deleted.append(key)
            self.values.pop(key, None)
            return True

        def make_key(self, key):
            return key

    fake_cache = _FakeCache()
    replacement_token = "worker-b-owner-token"
    acquired_tokens: list[str] = []

    def _replace_expired_lock(*, limit=None, now=None, ownership_guard=None):
        del limit, now, ownership_guard
        acquired_tokens.append(fake_cache.values[virtual_players.ROLL_LOCK_KEY])
        fake_cache.values[virtual_players.ROLL_LOCK_KEY] = replacement_token
        return 4

    monkeypatch.setattr(virtual_players, "cache", fake_cache, raising=False)
    monkeypatch.setattr(cache_lock, "cache", fake_cache)
    monkeypatch.setattr(
        cache_lock,
        "_release_cache_lock_atomic_if_owner",
        lambda *_a, **_k: cache_lock._AtomicCacheLockReleaseResult.NOT_OWNER,
    )
    monkeypatch.setattr(virtual_players, "_roll_virtual_player_population_unlocked", _replace_expired_lock)

    assert virtual_players.roll_virtual_player_population(limit=4, now=timezone.now()) == 4
    assert acquired_tokens and acquired_tokens[0] != replacement_token
    assert fake_cache.values[virtual_players.ROLL_LOCK_KEY] == replacement_token
    assert fake_cache.deleted == []


def test_population_roll_renews_periodically_and_stops_heartbeat(monkeypatch):
    from gameplay.services import virtual_players

    renew_calls: list[tuple[str, bool, str | None, int]] = []
    renewed_twice = threading.Event()
    released = threading.Event()

    monkeypatch.setattr(virtual_players, "ROLL_LOCK_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(
        virtual_players,
        "acquire_best_effort_lock",
        lambda *_args, **_kwargs: (True, True, "owner-token"),
    )

    def _renew(key, *, from_cache, lock_token, timeout_seconds, **_kwargs):
        renew_calls.append((key, from_cache, lock_token, timeout_seconds))
        if len(renew_calls) >= 2:
            renewed_twice.set()
        return True

    def _roll_unlocked(*, limit=None, now=None, ownership_guard=None):
        del limit, now
        assert ownership_guard is not None
        assert renewed_twice.wait(timeout=2)
        ownership_guard()
        return 4

    def _release(*_args, **_kwargs):
        released.set()

    monkeypatch.setattr(virtual_players, "renew_best_effort_lock", _renew, raising=False)
    monkeypatch.setattr(virtual_players, "release_best_effort_lock", _release)
    monkeypatch.setattr(virtual_players, "_roll_virtual_player_population_unlocked", _roll_unlocked)

    assert virtual_players.roll_virtual_player_population(limit=4, now=timezone.now()) == 4
    assert released.is_set()
    assert len(renew_calls) >= 2
    assert all(call == (virtual_players.ROLL_LOCK_KEY, True, "owner-token", 1) for call in renew_calls)

    renew_count_after_return = len(renew_calls)
    time.sleep(0.45)
    assert len(renew_calls) == renew_count_after_return


def test_population_roll_raises_precise_error_when_heartbeat_loses_lock(monkeypatch):
    from gameplay.services import virtual_players

    renew_attempted = threading.Event()
    released = threading.Event()

    monkeypatch.setattr(virtual_players, "ROLL_LOCK_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(
        virtual_players,
        "acquire_best_effort_lock",
        lambda *_args, **_kwargs: (True, True, "owner-token"),
    )

    def _lose_lock(*_args, **_kwargs):
        renew_attempted.set()
        return False

    def _roll_unlocked(*, limit=None, now=None, ownership_guard=None):
        del limit, now
        assert ownership_guard is not None
        assert renew_attempted.wait(timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            ownership_guard()
            time.sleep(0.01)
        raise AssertionError("ownership guard did not observe the lost lease")

    def _release(*_args, **_kwargs):
        released.set()

    monkeypatch.setattr(virtual_players, "renew_best_effort_lock", _lose_lock, raising=False)
    monkeypatch.setattr(virtual_players, "release_best_effort_lock", _release)
    monkeypatch.setattr(virtual_players, "_roll_virtual_player_population_unlocked", _roll_unlocked)

    with pytest.raises(
        virtual_players.VirtualPlayerPopulationLockLostError,
        match="virtual player population roll lock ownership was lost",
    ):
        virtual_players.roll_virtual_player_population(limit=4, now=timezone.now())

    assert released.is_set()


def test_population_roll_re_raises_heartbeat_programming_error_unchanged(monkeypatch):
    from gameplay.services import virtual_players

    programming_error = TypeError("broken renew contract")
    heartbeat_caught_error = threading.Event()
    released = threading.Event()
    renew_calls: list[None] = []
    persisted_writes: list[str] = []
    logged_messages: list[str] = []

    monkeypatch.setattr(virtual_players, "ROLL_LOCK_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(
        virtual_players,
        "acquire_best_effort_lock",
        lambda *_args, **_kwargs: (True, True, "owner-token"),
    )

    def _raise_programming_error(*_args, **_kwargs):
        renew_calls.append(None)
        raise programming_error

    def _capture_heartbeat_error(message, *_args, **_kwargs):
        logged_messages.append(message)
        heartbeat_caught_error.set()

    def _roll_unlocked(*, limit=None, now=None, ownership_guard=None):
        del limit, now
        assert ownership_guard is not None
        assert heartbeat_caught_error.wait(timeout=2)
        ownership_guard()
        persisted_writes.append("unexpected write")
        return 4

    def _release(*_args, **_kwargs):
        released.set()

    monkeypatch.setattr(virtual_players, "renew_best_effort_lock", _raise_programming_error)
    monkeypatch.setattr(virtual_players.logger, "exception", _capture_heartbeat_error)
    monkeypatch.setattr(virtual_players, "release_best_effort_lock", _release)
    monkeypatch.setattr(virtual_players, "_roll_virtual_player_population_unlocked", _roll_unlocked)

    raised_errors: list[Exception] = []
    try:
        virtual_players.roll_virtual_player_population(limit=4, now=timezone.now())
    except Exception as exc:
        raised_errors.append(exc)

    renew_count_after_return = len(renew_calls)
    time.sleep(0.45)
    assert len(renew_calls) == renew_count_after_return
    assert renew_count_after_return == 1
    assert released.is_set()
    assert persisted_writes == []
    assert raised_errors and raised_errors[0] is programming_error
    assert not isinstance(raised_errors[0], virtual_players.VirtualPlayerPopulationLockLostError)
    assert logged_messages == ["Virtual player population roll heartbeat raised an unexpected error"]


@pytest.mark.django_db
def test_population_roll_guard_preserves_first_create_and_stops_before_second(settings):
    from gameplay.models import BotProfile
    from gameplay.services import virtual_players

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 1,
            "hard_cap": 20,
            "rolling_batch_size": [2, 2],
        },
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        },
    }

    def _guard_after_first_commit():
        if BotProfile.objects.exists():
            raise virtual_players.VirtualPlayerPopulationLockLostError(
                "virtual player population roll lock ownership was lost"
            )

    with pytest.raises(virtual_players.VirtualPlayerPopulationLockLostError):
        virtual_players._roll_virtual_player_population_unlocked(
            limit=2,
            now=timezone.now(),
            ownership_guard=_guard_after_first_commit,
        )

    assert BotProfile.objects.count() == 1


def test_population_roll_fails_closed_when_cache_is_unavailable(monkeypatch):
    from django_redis.exceptions import ConnectionInterrupted

    from core.utils import cache_lock
    from gameplay.services import virtual_players

    class _BrokenCache:
        def add(self, *_args, **_kwargs):
            raise ConnectionInterrupted("population lock cache unavailable")

    broken_cache = _BrokenCache()
    monkeypatch.setattr(virtual_players, "cache", broken_cache, raising=False)
    monkeypatch.setattr(cache_lock, "cache", broken_cache)
    monkeypatch.setattr(
        virtual_players,
        "_roll_virtual_player_population_unlocked",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("population roll must fail closed")),
    )

    assert virtual_players.roll_virtual_player_population(limit=4, now=timezone.now()) == 0


@pytest.mark.django_db
def test_population_roll_returns_zero_when_lock_is_held(settings):
    from gameplay.services.virtual_players import roll_virtual_player_population

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 20, "min_attackable_per_band": 10, "hard_cap": 20},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    cache.add("virtual_players:roll_lock", "1", timeout=60)
    try:
        assert roll_virtual_player_population(limit=5, now=timezone.now()) == 0
    finally:
        cache.delete("virtual_players:roll_lock")


@pytest.mark.django_db
def test_due_maintenance_keeps_target_prestige_band_while_bot_grows(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 0, "min_attackable_per_band": 0, "hard_cap": 10},
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="south",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=23455,
        now=now,
        projection=BotProjectionConfig(prestige=1900, building_level=3, guest_count=0, guest_level=1),
    )
    profile.next_growth_at = now - timedelta(minutes=1)
    profile.save(update_fields=["next_growth_at"])

    assert profile.manor.prestige == 1900
    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.prestige_band == "junior"


@pytest.mark.django_db
def test_virtual_player_tracks_target_and_current_prestige_bands_separately(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 0, "min_attackable_per_band": 0, "hard_cap": 10},
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000], "middle": [2000, 8000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="south",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=23457,
        now=now,
        projection=BotProjectionConfig(prestige=1900, building_level=3, guest_count=0, guest_level=1),
    )

    assert profile.target_prestige_band == "junior"
    assert profile.current_prestige_band == "junior"

    profile.manor.prestige = 2500
    profile.manor.save(update_fields=["prestige"])
    profile.next_growth_at = now - timedelta(minutes=1)
    profile.save(update_fields=["next_growth_at"])

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.target_prestige_band == "junior"
    assert profile.current_prestige_band == "middle"
    assert profile.prestige_band == "junior"


@pytest.mark.django_db
def test_due_maintenance_syncs_current_band_after_growth_crosses_band(settings, django_user_model):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import maintain_due_virtual_players

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 0, "min_attackable_per_band": 0, "hard_cap": 10},
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    user = django_user_model.objects.create_user(username="bot_growth_band_cross", password="pass123")
    manor = user.manor
    manor.region = "south"
    manor.prestige = 250
    manor.save(update_fields=["region", "prestige"])
    now = timezone.now()
    BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="junior",
        target_prestige_band="junior",
        current_prestige_band="newbie",
        growth_seed=23459,
        growth_stage=1,
        next_growth_at=now - timedelta(minutes=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        loot_budget_daily=1000,
        last_planned_at=now - timedelta(hours=1),
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile = BotProfile.objects.get(manor=manor)
    assert profile.manor.prestige == 500
    assert profile.target_prestige_band == "junior"
    assert profile.current_prestige_band == "junior"
    assert profile.prestige_band == "junior"


@pytest.mark.django_db
def test_due_maintenance_syncs_profile_prestige_band_after_growth(settings):
    from gameplay.models import BotProfile
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_projection_templates()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 0, "min_attackable_per_band": 0, "hard_cap": 10},
        "prestige_bands": {"junior": [500, 2000], "middle": [2000, 8000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="south",
        prestige_band="junior",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=23456,
        now=now,
        projection=BotProjectionConfig(prestige=1900, building_level=3, guest_count=0, guest_level=1),
    )
    profile.manor.prestige = 2500
    profile.manor.save(update_fields=["prestige"])
    profile.next_growth_at = now - timedelta(minutes=1)
    profile.save(update_fields=["next_growth_at"])

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.prestige_band == "junior"
    assert profile.target_prestige_band == "junior"
    assert profile.current_prestige_band == "middle"
