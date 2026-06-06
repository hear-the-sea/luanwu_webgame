from __future__ import annotations

import logging
from datetime import timedelta
from itertools import count

import pytest
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from battle.models import TroopTemplate
from common.constants.resources import ResourceType
from gameplay.constants import BuildingKeys
from gameplay.models import Building, BuildingType, InventoryItem, ItemTemplate, Manor, PlayerTechnology
from gameplay.services.manor.core import calculate_building_capacity, ensure_manor
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestSkill,
    GuestTemplate,
    Skill,
)

_COUNTER = count(1)


def _unique(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


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
    assert manor.prestige == 100
    assert manor.newbie_protection_until is None
    assert manor.user.has_usable_password() is False

    silver_vault = manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT)
    granary = manor.buildings.get(building_type__key=BuildingKeys.GRANARY)
    assert silver_vault.level == 1
    assert granary.level == 1
    assert manor.silver_capacity == calculate_building_capacity(1, is_silver_vault=True)
    assert manor.grain_capacity == calculate_building_capacity(1, is_silver_vault=False)
    assert manor.silver == 5000
    assert manor.grain == 1200

    assert PlayerTechnology.objects.filter(manor=manor, level__gt=0).count() == 0
    assert Guest.objects.filter(manor=manor, level=1).count() == 1
    assert GearItem.objects.filter(manor=manor, guest__manor=manor).count() == 1
    assert all(guest.skills.exists() for guest in manor.guests.all())
    assert manor.troops.filter(count__gt=0).exists()
    created_log = next(
        record for record in caplog.records if getattr(record, "event", None) == "virtual_player_created"
    )
    assert created_log.region == "north"
    assert created_log.prestige_band == "junior"
    assert profile.target_prestige_band == "junior"
    assert profile.current_prestige_band == "newbie"
    assert created_log.archetype == BotProfile.Archetype.DOJO
    assert created_log.manor_id == manor.id


@pytest.mark.django_db
def test_create_virtual_player_starts_from_newbie_baseline_even_with_high_projection(settings):
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
    assert manor.prestige <= 250
    assert manor.silver == 5000
    assert manor.grain == 1200
    assert manor.buildings.get(building_type__key=BuildingKeys.SILVER_VAULT).level == 1
    assert manor.guests.count() <= 1
    assert all(guest.level == 1 for guest in manor.guests.all())
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
    assert set(profile.manor.guests.values_list("level", flat=True)) == {2}
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
            "low_stage_powerful_item_chance": 1.0,
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
            "low_stage_powerful_item_chance": 1.0,
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
            "powerful_item_daily_global_cap": 10,
            "powerful_item_min_price": 100_000,
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
    assert stock[valuable.key] == 2


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
def test_population_roll_counts_target_band_instead_of_current_prestige(settings):
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

    assert BotProfile.objects.get(target_prestige_band="junior").current_prestige_band == "newbie"
    assert roll_virtual_player_population(limit=20, now=now) == 19
    assert BotProfile.objects.filter(target_prestige_band="junior", manor__region="north").count() == 1


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

    assert profile.manor.prestige < 500
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
    assert profile.current_prestige_band == "newbie"

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
