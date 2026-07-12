from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from threading import Event, Thread
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from battle.models import TroopTemplate
from core.config import GUEST
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock, renew_best_effort_lock
from core.utils.yaml_loader import load_yaml_data
from gameplay.constants import REGION_CHOICES, BuildingKeys
from gameplay.models import (
    BotBackfillDemand,
    BotInventoryDailyCounter,
    BotProfile,
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
from gameplay.services.manor.coordinates import is_occupied_manor_location_conflict
from gameplay.services.manor.core import calculate_building_capacity, generate_unique_coordinate
from gameplay.services.manor.naming import ManorNameConflictError
from gameplay.services.technology_catalog import build_technology_index
from guests.models import GearItem, GearTemplate, Guest, GuestSkill, GuestTemplate, Skill
from guests.services.equipment_payloads import build_gear_template_defaults, build_gear_template_preview
from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest
from guests.services.recruitment_guests import create_guest_from_template

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotProjectionConfig:
    prestige: int
    building_level: int
    guest_count: int
    guest_level: int


DEFAULT_VIRTUAL_PLAYER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "population": {
        "active_player_multiplier": 4,
        "min_per_region": 20,
        "min_attackable_per_band": 10,
        "hard_cap": 2000,
        "rolling_batch_size": [3, 12],
    },
    "prestige_bands": {
        "newbie": [0, 500],
        "junior": [500, 2000],
        "middle": [2000, 8000],
        "senior": [8000, 30000],
        "veteran": [30000, None],
    },
    "lifecycle": {
        "active_days": [30, 90],
        "abandoned_days": [14, 45],
        "next_growth_hours": [2, 18],
        "empty_hit_stale_threshold": 3,
        "empty_hit_window_hours": 24,
        "stale_no_interaction_days": 30,
    },
    "resources": {
        "balanced": [0.25, 0.55],
        "rich": [0.55, 0.85],
        "dojo": [0.15, 0.40],
        "guard": [0.20, 0.45],
        "abandoned": [0.65, 0.95],
    },
    "projection": {
        "guest_template_keys": [],
        "gear_template_keys": [],
        "extra_skill_keys": [],
        "extra_skills_per_guest": [0, 0],
        "high_tier_skill_keys": [],
        "high_tier_skill_chance": 0.0,
        "high_tier_skills_per_guest": [1, 1],
        "troop_template_keys": [],
        "technology_keys": [],
        "loot_budget_daily": 2_000_000,
        "loot_limits": {
            "real_attacker_daily_resource_cap": 2_000_000,
        },
        "rare_item_daily_global_cap": 20,
        "powerful_item_daily_global_cap": 5,
        "powerful_item_min_price": 100_000,
        "powerful_item_min_growth_stage": 5,
        "low_stage_powerful_item_chance": 0.03,
        "powerful_item_prestige_chance": [
            {"min_prestige": 0, "chance": 0.0},
            {"min_prestige": 500, "chance": 0.01},
            {"min_prestige": 2000, "chance": 0.03},
            {"min_prestige": 8000, "chance": 0.06},
            {"min_prestige": 30000, "chance": 0.12},
        ],
    },
}

VIRTUAL_PLAYER_CONFIG_PATH = Path(settings.BASE_DIR) / "data" / "virtual_players.yaml"
ROLL_LOCK_KEY = "virtual_players:roll_lock"
ROLL_LOCK_TIMEOUT_SECONDS = 300
VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT = 5
RARE_ITEM_RARITIES = {"purple", "orange", "red", "legendary"}
ALL_TEMPLATE_SENTINEL = "__all__"
ALL_TRADEABLE_TEMPLATE_SENTINEL = "__all_tradeable__"

_MANOR_NAME_SURNAMES = (
    "沈",
    "陆",
    "顾",
    "萧",
    "谢",
    "苏",
    "林",
    "周",
    "温",
    "叶",
    "秦",
    "唐",
    "宋",
    "楚",
    "云",
    "白",
    "江",
    "许",
    "程",
    "傅",
)
_MANOR_NAME_GIVEN = (
    "清远",
    "怀瑾",
    "映雪",
    "知微",
    "明渊",
    "青岚",
    "景行",
    "疏桐",
    "云舟",
    "听澜",
    "照夜",
    "归鸿",
    "问渠",
    "少卿",
    "知白",
    "长风",
)
_MANOR_NAME_PREFIXES = (
    "青竹",
    "松月",
    "听雨",
    "归云",
    "临溪",
    "怀雪",
    "枕霞",
    "晴川",
    "墨泉",
    "竹隐",
    "云麓",
    "栖梧",
    "照水",
    "南柯",
    "北辰",
    "秋声",
)
_MANOR_NAME_SUFFIXES = (
    "山庄",
    "别院",
    "小筑",
    "草堂",
    "书斋",
    "雅舍",
    "庄园",
    "庭",
    "坞",
    "居",
    "轩",
    "庐",
)
_MANOR_NAME_INTERNET_PREFIXES = (
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
)
_MANOR_NAME_INTERNET_SUFFIXES = (
    "山庄",
    "别院",
    "小筑",
    "草堂",
    "轩",
    "居",
    "堂",
    "府",
    "园",
    "避难所",
)
_MANOR_NAME_INTERNET_STANDALONE = (
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
)

CORE_BUILDING_KEYS = (
    BuildingKeys.SILVER_VAULT,
    BuildingKeys.GRANARY,
    BuildingKeys.JUXIAN_ZHUANG,
    BuildingKeys.JIADING_FANG,
    BuildingKeys.YOUXIA_BAOTA,
    BuildingKeys.LIANGGONG_CHANG,
)
INITIAL_BOT_PRESTIGE = 100
INITIAL_BOT_BUILDING_LEVEL = 1
INITIAL_BOT_GUEST_COUNT = 1
INITIAL_BOT_GUEST_LEVEL = 1
LOW_STAGE_POWERFUL_ITEM_CUTOFF = 5


class VirtualPlayerPopulationLockLostError(RuntimeError):
    """Raised when a population roll no longer owns its distributed lock."""


def _deep_merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}
    for section, values in override.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section] = _deep_merge_config(merged[section], values)
        else:
            merged[section] = values
    return merged


@lru_cache(maxsize=1)
def _load_virtual_player_config_from_disk() -> dict[str, Any]:
    raw = load_yaml_data(VIRTUAL_PLAYER_CONFIG_PATH, logger=logger, context="virtual player config", default={})
    if not isinstance(raw, dict):
        raw = {}
    return _deep_merge_config(DEFAULT_VIRTUAL_PLAYER_CONFIG, raw)


def clear_virtual_player_config_cache() -> None:
    _load_virtual_player_config_from_disk.cache_clear()


def load_virtual_player_config() -> dict[str, Any]:
    config = _load_virtual_player_config_from_disk()
    configured = getattr(settings, "VIRTUAL_PLAYER_CONFIG", None)
    if not configured:
        return config
    return _deep_merge_config(config, configured)


def _range_value(rng: random.Random, values: Any, *, default: tuple[int, int]) -> int:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        low, high = default
    else:
        low, high = int(values[0]), int(values[1])
    if high < low:
        low, high = high, low
    return rng.randint(low, high)


def _range_float(rng: random.Random, values: Any, *, default: tuple[float, float]) -> float:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        low, high = default
    else:
        low, high = float(values[0]), float(values[1])
    if high < low:
        low, high = high, low
    return rng.uniform(low, high)


def _create_bot_user(*, region: str, growth_seed: int) -> Any:
    User = get_user_model()
    for attempt in range(20):
        suffix = f"{growth_seed}_{timezone.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
        username = f"bot_{region}_{suffix}"[:150]
        user = User(username=username, is_active=False)
        user.set_unusable_password()
        setattr(user, "_signup_region", region)
        setattr(user, "_signup_manor_name", _generate_bot_manor_name(growth_seed=growth_seed, salt=attempt))
        try:
            with transaction.atomic():
                user.save()
        except (IntegrityError, ManorNameConflictError):
            continue
        return user
    raise RuntimeError("Failed to allocate a unique bot manor name after multiple attempts")


def _generate_bot_manor_name(*, growth_seed: int, salt: int = 0) -> str:
    """Generate player-like manor names without visible system markers."""
    for attempt in range(400):
        rng = random.Random(f"{growth_seed}:{salt}:{attempt}")
        roll = rng.random()
        if roll < 0.38:
            candidate = rng.choice(_MANOR_NAME_INTERNET_STANDALONE)
        elif roll < 0.74:
            candidate = f"{rng.choice(_MANOR_NAME_INTERNET_PREFIXES)}{rng.choice(_MANOR_NAME_INTERNET_SUFFIXES)}"
        else:
            variant = attempt % 5
            if variant == 0:
                candidate = f"{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_GIVEN)}的庄园"
            elif variant == 1:
                candidate = f"{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_GIVEN)}的{rng.choice(_MANOR_NAME_SUFFIXES)}"
            elif variant == 2:
                candidate = f"{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
            elif variant == 3:
                candidate = f"{rng.choice(_MANOR_NAME_GIVEN)}{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
            else:
                candidate = f"{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_GIVEN)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
        if not Manor.objects.filter(name=candidate).exists():
            return candidate

    fallback_rng = random.Random(uuid.uuid4().hex)
    for _ in range(400):
        candidate = (
            f"{fallback_rng.choice(_MANOR_NAME_SURNAMES)}"
            f"{fallback_rng.choice(_MANOR_NAME_GIVEN)}"
            f"{fallback_rng.choice(_MANOR_NAME_PREFIXES)}"
            f"{fallback_rng.choice(_MANOR_NAME_SUFFIXES)}"
        )
        if not Manor.objects.filter(name=candidate).exists():
            return candidate
    raise RuntimeError("Failed to generate a unique bot manor name")


def _set_unique_location(manor: Manor, *, region: str) -> None:
    x, y = generate_unique_coordinate(region)
    manor.region = region
    manor.coordinate_x = x
    manor.coordinate_y = y


def _save_virtual_player_manor_with_coordinate_retry(
    manor: Manor,
    *,
    region: str,
    update_fields: list[str],
) -> None:
    for attempt in range(VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT):
        try:
            with transaction.atomic():
                manor.save(update_fields=update_fields)
            return
        except IntegrityError as exc:
            if not is_occupied_manor_location_conflict(exc):
                raise
            if attempt + 1 >= VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT:
                raise
            _set_unique_location(manor, region=region)


def _project_buildings(manor: Manor, *, level: int) -> None:
    building_types = list(BuildingType.objects.filter(key__in=CORE_BUILDING_KEYS))
    existing_by_type = {row.building_type_id: row for row in manor.buildings.filter(building_type__in=building_types)}
    to_create: list[Building] = []
    to_update: list[Building] = []
    for building_type in building_types:
        building = existing_by_type.get(building_type.id)
        if building is None:
            to_create.append(Building(manor=manor, building_type=building_type, level=level))
        else:
            building.level = max(1, int(level))
            building.is_upgrading = False
            building.upgrade_complete_at = None
            to_update.append(building)
    if to_create:
        Building.objects.bulk_create(to_create)
    if to_update:
        Building.objects.bulk_update(to_update, ["level", "is_upgrading", "upgrade_complete_at"])

    manor.invalidate_building_cache()
    manor.silver_capacity = calculate_building_capacity(level, is_silver_vault=True)
    manor.grain_capacity = calculate_building_capacity(level, is_silver_vault=False)


def _resource_fill_for(archetype: str, rng: random.Random, config: dict[str, Any]) -> float:
    resource_config = config.get("resources") or {}
    values = resource_config.get(archetype) or resource_config.get(BotProfile.Archetype.BALANCED)
    return _range_float(rng, values, default=(0.25, 0.55))


def _project_resources(manor: Manor, *, archetype: str, rng: random.Random, config: dict[str, Any]) -> None:
    fill = _resource_fill_for(archetype, rng, config)
    manor.silver = max(1, min(manor.silver_capacity, int(manor.silver_capacity * fill)))
    manor.grain = max(1, min(manor.grain_capacity, int(manor.grain_capacity * fill)))


def _configured_keys(config: dict[str, Any], field: str) -> list[str]:
    projection = config.get("projection") or {}
    raw = projection.get(field) or []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(item) for item in raw if item]


def _configured_model_keys(
    config: dict[str, Any],
    field: str,
    model,
) -> list[str]:
    keys = _configured_keys(config, field)
    if ALL_TEMPLATE_SENTINEL not in keys:
        return keys
    queryset = model.objects.all()
    return list(queryset.order_by("key").values_list("key", flat=True))


def _configured_item_keys(config: dict[str, Any], field: str) -> list[str]:
    keys = _configured_keys(config, field)
    if ALL_TEMPLATE_SENTINEL not in keys and ALL_TRADEABLE_TEMPLATE_SENTINEL not in keys:
        return keys
    return list(ItemTemplate.objects.filter(tradeable=True).order_by("key").values_list("key", flat=True))


def _configured_technology_keys(config: dict[str, Any]) -> list[str]:
    keys = _configured_keys(config, "technology_keys")
    if ALL_TEMPLATE_SENTINEL not in keys:
        return keys
    return sorted(build_technology_index().keys())


def _regions() -> list[str]:
    return [key for key, _label in REGION_CHOICES if key != "overseas"]


def _prestige_bands(config: dict[str, Any]) -> dict[str, tuple[int, int | None]]:
    bands: dict[str, tuple[int, int | None]] = {}
    for key, raw_range in (config.get("prestige_bands") or {}).items():
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        low = max(0, int(raw_range[0] or 0))
        high = None if raw_range[1] is None else max(low, int(raw_range[1]))
        bands[str(key)] = (low, high)
    return bands


def _band_filter_kwargs(low: int, high: int | None, *, prefix: str = "") -> dict[str, Any]:
    kwargs: dict[str, Any] = {f"{prefix}prestige__gte": low}
    if high is not None:
        kwargs[f"{prefix}prestige__lt"] = high
    return kwargs


def _prestige_band_for_value(prestige: int, config: dict[str, Any]) -> str | None:
    for band_name, (low, high) in _prestige_bands(config).items():
        if int(prestige) >= low and (high is None or int(prestige) < high):
            return band_name
    return None


def _profile_target_prestige_band(profile: BotProfile) -> str:
    return str(profile.target_prestige_band or profile.prestige_band)


def _target_band_filter(prestige_band: str) -> Q:
    return Q(target_prestige_band=str(prestige_band)) | Q(target_prestige_band="", prestige_band=str(prestige_band))


def record_virtual_player_backfill_demand(*, region: str, prestige_band: str, needed: int) -> None:
    """Record an async bot backfill demand for later rolling population work."""
    needed = max(0, int(needed or 0))
    if needed <= 0 or not region or not prestige_band:
        return
    normalized_region = str(region)
    normalized_band = str(prestige_band)
    with transaction.atomic():
        demand, created = BotBackfillDemand.objects.select_for_update().get_or_create(
            region=normalized_region,
            prestige_band=normalized_band,
            defaults={"needed": needed},
        )
        if not created and needed > int(demand.needed or 0):
            demand.needed = needed
            demand.save(update_fields=["needed", "updated_at"])


def consume_virtual_player_backfill_demands(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Pop recorded backfill demands in deterministic order."""
    queryset = BotBackfillDemand.objects.select_for_update().order_by("region", "prestige_band", "id")
    if limit is not None:
        queryset = queryset[: max(0, int(limit))]
    with transaction.atomic():
        rows = list(queryset)
        if not rows:
            return []
        consumed = [
            {"region": row.region, "prestige_band": row.prestige_band, "needed": int(row.needed or 0)}
            for row in rows
            if int(row.needed or 0) > 0
        ]
        BotBackfillDemand.objects.filter(id__in=[row.id for row in rows]).delete()
        return consumed


def record_virtual_player_backfill_demand_for_search(
    *,
    searcher: Manor,
    region: str,
    candidate_count: int,
) -> None:
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return
    population = config.get("population") or {}
    min_per_band = max(0, int(population.get("min_attackable_per_band", 0) or 0))
    if min_per_band <= 0:
        return
    prestige_band = _prestige_band_for_value(int(searcher.prestige or 0), config)
    if prestige_band is None:
        return
    deficit = max(0, min_per_band - max(0, int(candidate_count or 0)))
    record_virtual_player_backfill_demand(region=region, prestige_band=prestige_band, needed=deficit)


def _active_real_player_count(now) -> int:
    active_after = now - timedelta(days=7)
    return Manor.objects.filter(
        bot_profile__isnull=True,
        user__is_staff=False,
        user__is_superuser=False,
        last_active_at__gte=active_after,
    ).count()


def _maintained_bot_queryset():
    return BotProfile.objects.exclude(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED])


def _maintained_bot_count() -> int:
    return _maintained_bot_queryset().count()


def _target_bot_total(config: dict[str, Any], *, now) -> int:
    population = config.get("population") or {}
    multiplier = max(0, int(population.get("active_player_multiplier", 0) or 0))
    target = _active_real_player_count(now) * multiplier
    hard_cap = int(population.get("hard_cap", 0) or 0)
    if hard_cap > 0:
        target = min(target, hard_cap)
    return max(0, target)


def _projection_from_real_players(
    *,
    region: str | None,
    low: int,
    high: int | None,
    rng: random.Random,
) -> BotProjectionConfig | None:
    filters = _band_filter_kwargs(low, high)
    base_qs = Manor.objects.filter(
        bot_profile__isnull=True,
        user__is_staff=False,
        user__is_superuser=False,
        **filters,
    )
    regional_qs = base_qs.filter(region=region) if region else Manor.objects.none()
    qs = regional_qs if regional_qs.exists() else base_qs
    count = qs.count()
    if count <= 0:
        return None

    manor = qs.order_by("id")[rng.randrange(count)]
    building_level = (
        manor.buildings.filter(building_type__key__in=CORE_BUILDING_KEYS).aggregate(max_level=Max("level"))["max_level"]
        or 1
    )
    guest_stats = manor.guests.aggregate(count=Count("id"), max_level=Max("level"))
    guest_count = max(1, min(8, int(guest_stats["count"] or 1)))
    guest_level = max(1, int(guest_stats["max_level"] or building_level))

    prestige = int(manor.prestige or low)
    if prestige > 0:
        jitter = max(1, int(prestige * 0.1))
        prestige += rng.randint(-jitter, jitter)
    upper = high if high is not None else max(low + 1, prestige + 1)
    prestige = max(low, min(max(low, upper - 1), prestige))

    return BotProjectionConfig(
        prestige=prestige,
        building_level=max(1, int(building_level)),
        guest_count=guest_count,
        guest_level=guest_level,
    )


def _projection_for_band(
    band: str,
    low: int,
    high: int | None,
    rng: random.Random,
    *,
    region: str | None = None,
) -> BotProjectionConfig:
    sampled = _projection_from_real_players(region=region, low=low, high=high, rng=rng)
    if sampled is not None:
        return sampled

    upper = high or max(low + 1, 50000)
    prestige = rng.randint(low, max(low, upper - 1))
    band_level = {
        "newbie": 2,
        "junior": 5,
        "middle": 9,
        "senior": 14,
        "veteran": 18,
    }.get(band, max(2, min(18, int((prestige / 2000) + 3))))
    guest_count = max(1, min(8, band_level // 2))
    guest_level = max(1, band_level + rng.randint(-1, 2))
    return BotProjectionConfig(
        prestige=prestige,
        building_level=band_level,
        guest_count=guest_count,
        guest_level=guest_level,
    )


def _weighted_archetype(rng: random.Random) -> str:
    weighted = [
        (BotProfile.Archetype.BALANCED, 35),
        (BotProfile.Archetype.RICH, 25),
        (BotProfile.Archetype.DOJO, 15),
        (BotProfile.Archetype.GUARD, 15),
        (BotProfile.Archetype.ABANDONED, 10),
    ]
    total = sum(weight for _key, weight in weighted)
    roll = rng.randint(1, total)
    current = 0
    for key, weight in weighted:
        current += weight
        if roll <= current:
            return key
    return BotProfile.Archetype.BALANCED


def virtual_player_prestige_bands(config: dict[str, Any] | None = None) -> dict[str, tuple[int, int | None]]:
    return _prestige_bands(config or load_virtual_player_config())


def create_virtual_players_for_band(
    *,
    region: str,
    prestige_band: str,
    count: int,
    archetype: str | None = None,
    now=None,
) -> list[BotProfile]:
    count = int(count)
    if count <= 0:
        raise ValueError("count must be positive")

    now = now or timezone.now()
    bands = virtual_player_prestige_bands()
    if prestige_band not in bands:
        raise ValueError(f"unknown prestige band: {prestige_band}")

    rng = random.Random(int(now.timestamp()))
    low, high = bands[prestige_band]
    profiles: list[BotProfile] = []
    for _idx in range(count):
        seed = rng.randint(1, 2_147_483_647)
        profiles.append(
            create_virtual_player(
                region=region,
                prestige_band=prestige_band,
                archetype=archetype or _weighted_archetype(rng),
                growth_seed=seed,
                now=now,
                projection=_projection_for_band(prestige_band, low, high, rng, region=region),
            )
        )
    return profiles


def _project_technologies(manor: Manor, *, level: int, config: dict[str, Any]) -> None:
    keys = _configured_technology_keys(config)
    if not keys:
        return
    rows = [PlayerTechnology(manor=manor, tech_key=key, level=max(0, int(level)), is_upgrading=False) for key in keys]
    PlayerTechnology.objects.bulk_create(
        rows,
        update_conflicts=True,
        update_fields=["level", "is_upgrading"],
        unique_fields=["manor", "tech_key"],
    )


def _grant_extra_template_skills(guest: Guest) -> None:
    existing = set(guest.guest_skills.values_list("skill_id", flat=True))
    rows: list[GuestSkill] = []
    for skill in guest.template.initial_skills.exclude(id__in=existing):
        rows.append(GuestSkill(guest=guest, skill=skill, source=GuestSkill.Source.TEMPLATE))
    if rows:
        GuestSkill.objects.bulk_create(rows, ignore_conflicts=True)


def _guest_meets_skill_requirements(guest: Guest, skill: Skill) -> bool:
    return (
        int(guest.level or 0) >= int(skill.required_level or 0)
        and int(guest.force or 0) >= int(skill.required_force or 0)
        and int(guest.intellect or 0) >= int(skill.required_intellect or 0)
        and int(guest.defense_stat or 0) >= int(skill.required_defense or 0)
        and int(guest.agility or 0) >= int(skill.required_agility or 0)
    )


def _grant_skills_from_pool(
    guest: Guest,
    *,
    rng: random.Random,
    skill_keys: list[str],
    target_count: int,
) -> int:
    if not skill_keys:
        return 0
    if target_count <= 0:
        return 0

    existing_ids = set(guest.guest_skills.values_list("skill_id", flat=True))
    remaining_slots = max(0, int(GUEST.MAX_SKILL_SLOTS) - len(existing_ids))
    if remaining_slots <= 0:
        return 0

    skills = list(Skill.objects.filter(key__in=skill_keys).exclude(id__in=existing_ids))
    rng.shuffle(skills)
    rows: list[GuestSkill] = []
    for skill in skills:
        if len(rows) >= min(target_count, remaining_slots):
            break
        if not _guest_meets_skill_requirements(guest, skill):
            continue
        rows.append(GuestSkill(guest=guest, skill=skill, source=GuestSkill.Source.BOOK))

    if rows:
        GuestSkill.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def _chance_value(value: Any, *, default: float = 0.0) -> float:
    try:
        chance = float(value)
    except (TypeError, ValueError):
        chance = default
    return max(0.0, min(1.0, chance))


def _grant_configured_extra_skills(guest: Guest, *, rng: random.Random, config: dict[str, Any]) -> None:
    projection = config.get("projection") or {}
    high_tier_chance = _chance_value(projection.get("high_tier_skill_chance"), default=0.0)
    if high_tier_chance > 0 and rng.random() < high_tier_chance:
        high_tier_count = _range_value(rng, projection.get("high_tier_skills_per_guest"), default=(1, 1))
        _grant_skills_from_pool(
            guest,
            rng=rng,
            skill_keys=_configured_keys(config, "high_tier_skill_keys"),
            target_count=high_tier_count,
        )

    target_count = _range_value(rng, projection.get("extra_skills_per_guest"), default=(0, 0))
    _grant_skills_from_pool(
        guest,
        rng=rng,
        skill_keys=_configured_keys(config, "extra_skill_keys"),
        target_count=target_count,
    )


def _equip_template(guest: Guest, template: GearTemplate) -> None:
    gear = GearItem.objects.create(manor=guest.manor, template=template, guest=guest)
    updates = {"attack_bonus", "defense_bonus"}
    apply_template_stats_to_guest(guest, gear.template, +1, updates)
    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)


def _gear_slots_for_archetype(archetype: str, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("gear_slots_by_archetype") or {}
    if isinstance(configured, dict) and archetype in configured:
        return max(0, int(configured[archetype] or 0))
    default_slots: dict[str, int] = {
        BotProfile.Archetype.DOJO.value: 2,
        BotProfile.Archetype.GUARD.value: 1,
        BotProfile.Archetype.RICH.value: 1,
        BotProfile.Archetype.ABANDONED.value: 0,
    }
    return default_slots.get(archetype, 1)


def _configured_gear_templates(config: dict[str, Any]) -> list[GearTemplate]:
    keys = _configured_model_keys(config, "gear_template_keys", GearTemplate)
    if not keys:
        return []
    unique_keys = list(dict.fromkeys(keys))
    templates_by_key = {template.key: template for template in GearTemplate.objects.filter(key__in=unique_keys)}
    missing_keys = [key for key in unique_keys if key not in templates_by_key]
    if missing_keys:
        item_templates = ItemTemplate.objects.filter(key__in=missing_keys, effect_type__startswith="equip_")
        for item_template in item_templates:
            preview = build_gear_template_preview(item_template)
            if preview is None:
                continue
            template, _created = GearTemplate.objects.update_or_create(
                key=item_template.key,
                defaults=build_gear_template_defaults(item_template, slot=preview.slot),
            )
            templates_by_key[template.key] = template
    return [templates_by_key[key] for key in unique_keys if key in templates_by_key]


def _project_guests_and_gear(
    manor: Manor,
    *,
    count: int,
    level: int,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
    grant_configured_skills: bool = True,
) -> None:
    guest_keys = _configured_model_keys(config, "guest_template_keys", GuestTemplate)
    if not guest_keys or count <= 0:
        return
    templates = list(GuestTemplate.objects.filter(key__in=guest_keys).prefetch_related("initial_skills"))
    if not templates:
        return
    gear_templates = _configured_gear_templates(config)
    for idx in range(max(0, int(count))):
        template = templates[idx % len(templates)]
        guest = create_guest_from_template(manor=manor, template=template, rng=rng, grant_skills=True)
        guest.level = max(1, int(level))
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["level", "current_hp"])
        _grant_extra_template_skills(guest)
        if grant_configured_skills:
            _grant_configured_extra_skills(guest, rng=rng, config=config)
        gear_slots = min(_gear_slots_for_archetype(str(archetype), config), len(gear_templates))
        for gear_offset in range(gear_slots):
            _equip_template(guest, gear_templates[(idx + gear_offset) % len(gear_templates)])


def _project_troops(manor: Manor, *, count: int, config: dict[str, Any]) -> None:
    troop_keys = _configured_model_keys(config, "troop_template_keys", TroopTemplate)
    if not troop_keys:
        return
    templates = list(TroopTemplate.objects.filter(key__in=troop_keys))
    if not templates:
        return
    per_type = max(1, int(count))
    rows = [PlayerTroop(manor=manor, troop_template=template, count=per_type) for template in templates]
    PlayerTroop.objects.bulk_create(
        rows,
        update_conflicts=True,
        update_fields=["count"],
        unique_fields=["manor", "troop_template"],
    )


def _inventory_quantity_multiplier(archetype: str, config: dict[str, Any]) -> float:
    projection = config.get("projection") or {}
    configured = projection.get("inventory_quantity_multipliers") or {}
    if isinstance(configured, dict) and archetype in configured:
        return max(0.0, float(configured[archetype] or 0))
    default_multipliers: dict[str, float] = {
        BotProfile.Archetype.RICH.value: 2.0,
        BotProfile.Archetype.ABANDONED.value: 2.5,
        BotProfile.Archetype.DOJO.value: 1.2,
        BotProfile.Archetype.GUARD.value: 1.0,
    }
    return default_multipliers.get(archetype, 1.0)


def _is_powerful_item(template: ItemTemplate, config: dict[str, Any]) -> bool:
    projection = config.get("projection") or {}
    powerful_min_price = int(projection.get("powerful_item_min_price") or 100_000)
    return int(template.price or 0) >= powerful_min_price


def _low_stage_powerful_item_chance(config: dict[str, Any]) -> float:
    projection = config.get("projection") or {}
    return _chance_value(projection.get("low_stage_powerful_item_chance"), default=0.03)


def _powerful_item_min_growth_stage(config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    return max(0, int(projection.get("powerful_item_min_growth_stage") or 0))


def _powerful_item_prestige_chance(config: dict[str, Any], prestige: int) -> float:
    projection = config.get("projection") or {}
    raw = projection.get("powerful_item_prestige_chance")
    if not isinstance(raw, list):
        return 0.0

    best_chance = 0.0
    best_min = -1
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            min_prestige = int(row.get("min_prestige", 0) or 0)
        except (TypeError, ValueError):
            continue
        chance = _chance_value(row.get("chance"), default=0.0)
        if prestige >= min_prestige and min_prestige >= best_min:
            best_min = min_prestige
            best_chance = chance
    return best_chance


def _bot_inventory_target_quantity(
    *,
    level: int,
    template: ItemTemplate,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
) -> int:
    projection = config.get("projection") or {}
    default_quantity = max(1, int(level) // 2)
    quantity_config = projection.get("loot_item_quantity")
    quantity = _range_value(rng, quantity_config, default=(default_quantity, default_quantity))
    quantity = int(quantity * _inventory_quantity_multiplier(str(archetype), config))
    return max(0, quantity)


def _should_project_inventory_template(
    template: ItemTemplate,
    *,
    level: int,
    growth_stage: int,
    prestige: int,
    rng: random.Random,
    config: dict[str, Any],
) -> bool:
    is_powerful = _is_powerful_item(template, config)
    is_rare = str(template.rarity or "").lower() in RARE_ITEM_RARITIES
    min_stage = _powerful_item_min_growth_stage(config)
    if min_stage > 0 and int(growth_stage or 0) < min_stage and (is_powerful or is_rare):
        return False
    if int(level or 0) > LOW_STAGE_POWERFUL_ITEM_CUTOFF:
        if is_powerful or is_rare:
            return rng.random() < _powerful_item_prestige_chance(config, int(prestige or 0))
        return True
    if not is_powerful and not is_rare:
        return True
    return rng.random() < min(
        _low_stage_powerful_item_chance(config),
        _powerful_item_prestige_chance(config, int(prestige or 0)),
    )


def _replenish_inventory_stock(
    manor: Manor,
    *,
    level: int,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
    growth_stage: int,
    prestige: int,
    now=None,
) -> None:
    keys = [
        *_configured_item_keys(config, "item_template_keys"),
        *_configured_item_keys(config, "loot_item_template_keys"),
    ]
    if not keys:
        return

    unique_keys = list(dict.fromkeys(keys))
    templates = list(ItemTemplate.objects.filter(key__in=unique_keys, tradeable=True).order_by("key"))
    if not templates:
        return
    rng.shuffle(templates)

    now = now or timezone.now()
    existing_by_template = {
        item.template_id: item
        for item in InventoryItem.objects.select_for_update().filter(
            manor=manor,
            template__in=templates,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
    }
    for template in templates:
        if not _should_project_inventory_template(
            template,
            level=level,
            growth_stage=growth_stage,
            prestige=prestige,
            rng=rng,
            config=config,
        ):
            continue
        target_quantity = _bot_inventory_target_quantity(
            level=level,
            template=template,
            rng=rng,
            config=config,
            archetype=str(archetype),
        )
        existing = existing_by_template.get(template.id)
        current_quantity = int(existing.quantity or 0) if existing is not None else 0
        needed = max(0, target_quantity - current_quantity)
        needed = _apply_inventory_daily_caps(template, quantity=needed, config=config, now=now)
        if needed <= 0:
            continue
        if existing is not None:
            existing.quantity = current_quantity + needed
            existing.save(update_fields=["quantity", "updated_at"])
            continue
        InventoryItem.objects.create(
            manor=manor,
            template=template,
            quantity=needed,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )


def _reserve_inventory_daily_cap(*, category: str, requested: int, cap: int, now) -> int:
    requested = max(0, int(requested or 0))
    cap = max(0, int(cap or 0))
    if requested <= 0 or cap <= 0:
        return requested

    counter_date = timezone.localtime(now).date()
    counter = _lock_inventory_daily_counter(category=str(category), counter_date=counter_date)
    allowed = min(requested, max(0, cap - int(counter.quantity or 0)))
    if allowed > 0:
        counter.quantity = int(counter.quantity or 0) + allowed
        counter.save(update_fields=["quantity", "updated_at"])
    if allowed < requested:
        logger.info(
            "Virtual player inventory cap truncated: category=%s requested=%s allowed=%s cap=%s date=%s",
            category,
            requested,
            allowed,
            cap,
            counter_date.isoformat(),
            extra={
                "event": "virtual_player_inventory_cap_truncated",
                "category": str(category),
                "requested": requested,
                "allowed": allowed,
                "cap": cap,
                "date": counter_date.isoformat(),
            },
        )
    return allowed


def _lock_inventory_daily_counter(*, category: str, counter_date):
    locked = BotInventoryDailyCounter.objects.select_for_update()
    try:
        counter, _created = locked.get_or_create(
            category=category,
            counter_date=counter_date,
            defaults={"quantity": 0},
        )
    except IntegrityError:
        counter = BotInventoryDailyCounter.objects.select_for_update().get(
            category=category,
            counter_date=counter_date,
        )
    return counter


def _release_inventory_daily_cap(*, category: str, amount: int, now) -> None:
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return
    counter_date = timezone.localtime(now).date()
    counter = (
        BotInventoryDailyCounter.objects.select_for_update()
        .filter(category=str(category), counter_date=counter_date)
        .first()
    )
    if counter is None:
        return
    counter.quantity = max(0, int(counter.quantity or 0) - amount)
    counter.save(update_fields=["quantity", "updated_at"])


def _apply_inventory_daily_caps(
    template: ItemTemplate,
    *,
    quantity: int,
    config: dict[str, Any],
    now,
) -> int:
    projection = config.get("projection") or {}
    quantity = max(0, int(quantity or 0))
    reservations: list[tuple[str, int]] = []

    checks: list[tuple[str, int]] = []
    if str(template.rarity or "").lower() in RARE_ITEM_RARITIES:
        checks.append(("rare", int(projection.get("rare_item_daily_global_cap") or 0)))
    powerful_min_price = int(projection.get("powerful_item_min_price") or 100_000)
    if int(template.price or 0) >= powerful_min_price:
        checks.append(("powerful", int(projection.get("powerful_item_daily_global_cap") or 0)))

    for category, cap in checks:
        previous_quantity = quantity
        quantity = _reserve_inventory_daily_cap(category=category, requested=quantity, cap=cap, now=now)
        if previous_quantity > quantity:
            for reserved_category, reserved_amount in reservations:
                _release_inventory_daily_cap(
                    category=reserved_category,
                    amount=min(reserved_amount, previous_quantity - quantity),
                    now=now,
                )
        if quantity <= 0:
            return 0
        reservations.append((category, quantity))
    return quantity


def _backfill_historical_timestamps(*, user: Any, manor: Manor, profile: BotProfile, rng: random.Random, now) -> None:
    historical_created_at = manor.last_active_at - timedelta(days=rng.randint(1, 30), hours=rng.randint(0, 23))

    user.__class__.objects.filter(pk=user.pk).update(date_joined=historical_created_at, last_login=manor.last_active_at)
    Manor.objects.filter(pk=manor.pk).update(created_at=historical_created_at)
    Building.objects.filter(manor=manor).update(created_at=historical_created_at, hp_updated_at=manor.last_active_at)
    Guest.objects.filter(manor=manor).update(created_at=historical_created_at, last_hp_recovery_at=manor.last_active_at)
    GearItem.objects.filter(manor=manor).update(acquired_at=historical_created_at)
    InventoryItem.objects.filter(manor=manor).update(created_at=historical_created_at, updated_at=historical_created_at)
    BotProfile.objects.filter(pk=profile.pk).update(updated_at=now)

    user.date_joined = historical_created_at
    user.last_login = manor.last_active_at
    manor.created_at = historical_created_at


def _lifecycle_dates(now, rng: random.Random, config: dict[str, Any]) -> tuple[Any, Any, Any]:
    lifecycle = config.get("lifecycle") or {}
    active_days = _range_value(rng, lifecycle.get("active_days"), default=(30, 90))
    abandoned_days = _range_value(rng, lifecycle.get("abandoned_days"), default=(14, 45))
    next_growth_hours = _range_value(rng, lifecycle.get("next_growth_hours"), default=(2, 18))
    abandon_at = now + timedelta(days=active_days)
    retire_at = abandon_at + timedelta(days=abandoned_days)
    next_growth_at = now + timedelta(hours=next_growth_hours, minutes=rng.randint(0, 59))
    return next_growth_at, abandon_at, retire_at


@transaction.atomic
def create_virtual_player(
    *,
    region: str,
    prestige_band: str,
    archetype: str = BotProfile.Archetype.BALANCED,
    growth_seed: int | None = None,
    now=None,
    projection: BotProjectionConfig | None = None,
) -> BotProfile:
    now = now or timezone.now()
    seed = int(growth_seed or random.randint(1, 2_147_483_647))
    rng = random.Random(seed)
    config = load_virtual_player_config()
    projection = projection or BotProjectionConfig(
        prestige=500,
        building_level=3,
        guest_count=2,
        guest_level=3,
    )
    target_band = _prestige_bands(config).get(prestige_band)
    target_low = target_band[0] if target_band is not None else 0
    if target_low > 0:
        target_high = target_band[1] if target_band is not None else None
        projected_prestige = max(target_low, int(projection.prestige))
        if target_high is not None:
            projected_prestige = min(projected_prestige, target_high - 1)
        starting_projection = BotProjectionConfig(
            prestige=projected_prestige,
            building_level=max(1, int(projection.building_level)),
            guest_count=max(0, int(projection.guest_count)),
            guest_level=max(1, int(projection.guest_level)),
        )
    else:
        starting_projection = BotProjectionConfig(
            prestige=min(max(0, int(projection.prestige)), INITIAL_BOT_PRESTIGE),
            building_level=INITIAL_BOT_BUILDING_LEVEL,
            guest_count=min(max(0, int(projection.guest_count)), INITIAL_BOT_GUEST_COUNT),
            guest_level=INITIAL_BOT_GUEST_LEVEL,
        )

    user = _create_bot_user(region=region, growth_seed=seed)
    manor = user.manor
    _set_unique_location(manor, region=region)
    manor.prestige = max(0, int(starting_projection.prestige))
    manor.newbie_protection_until = None
    manor.defeat_protection_until = None
    manor.peace_shield_until = None
    _project_buildings(manor, level=max(1, int(starting_projection.building_level)))
    manor.silver = 5000
    manor.grain = 1200
    manor.resource_updated_at = now
    manor.last_active_at = now - timedelta(days=rng.randint(3, 180), hours=rng.randint(0, 23))
    _save_virtual_player_manor_with_coordinate_retry(
        manor,
        region=region,
        update_fields=[
            "region",
            "coordinate_x",
            "coordinate_y",
            "prestige",
            "newbie_protection_until",
            "defeat_protection_until",
            "peace_shield_until",
            "silver_capacity",
            "grain_capacity",
            "silver",
            "grain",
            "resource_updated_at",
            "last_active_at",
        ],
    )

    _project_technologies(manor, level=0, config=config)
    _project_guests_and_gear(
        manor,
        count=starting_projection.guest_count,
        level=starting_projection.guest_level,
        rng=rng,
        config=config,
        archetype=str(archetype),
        grant_configured_skills=False,
    )
    _project_troops(manor, count=50, config=config)

    next_growth_at, abandon_at, retire_at = _lifecycle_dates(now, rng, config)
    profile = BotProfile.objects.create(
        manor=manor,
        archetype=archetype,
        state=BotProfile.State.ACTIVE,
        prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        current_prestige_band=_prestige_band_for_value(int(manor.prestige or 0), config) or prestige_band,
        growth_seed=seed,
        growth_stage=INITIAL_BOT_BUILDING_LEVEL,
        next_growth_at=next_growth_at,
        abandon_at=abandon_at,
        retire_at=retire_at,
        loot_budget_daily=int((config.get("projection") or {}).get("loot_budget_daily", 2_000_000) or 0),
        last_planned_at=now,
    )
    _backfill_historical_timestamps(user=user, manor=manor, profile=profile, rng=rng, now=now)
    logger.info(
        "Virtual player created: region=%s prestige_band=%s archetype=%s manor_id=%s",
        region,
        prestige_band,
        archetype,
        manor.id,
        extra={
            "event": "virtual_player_created",
            "region": region,
            "prestige_band": prestige_band,
            "archetype": archetype,
            "manor_id": manor.id,
        },
    )
    return profile


def is_bot_manor(manor: Manor) -> bool:
    return hasattr(manor, "bot_profile")


def _next_growth_time(now, profile: BotProfile, rng: random.Random, config: dict[str, Any]):
    lifecycle = config.get("lifecycle") or {}
    hours = _range_value(rng, lifecycle.get("next_growth_hours"), default=(2, 18))
    if profile.state == BotProfile.State.SLOWING:
        hours *= 2
    return now + timedelta(hours=hours, minutes=rng.randint(0, 59))


def _maintenance_projection_from_real_players(
    profile: BotProfile,
    *,
    rng: random.Random,
    config: dict[str, Any],
) -> BotProjectionConfig | None:
    band_range = _prestige_bands(config).get(_profile_target_prestige_band(profile))
    if band_range is None:
        return None
    low, high = band_range
    return _projection_from_real_players(region=profile.manor.region, low=low, high=high, rng=rng)


def _sync_profile_prestige_band(profile: BotProfile, *, config: dict[str, Any]) -> None:
    current_band = _prestige_band_for_value(int(profile.manor.prestige or 0), config)
    if not current_band or current_band == profile.current_prestige_band:
        return

    profile.current_prestige_band = current_band
    profile.save(update_fields=["current_prestige_band", "updated_at"])


def _maintain_active_profile(profile: BotProfile, *, now, config: dict[str, Any]) -> None:
    rng = random.Random(profile.growth_seed + profile.growth_stage)
    manor = profile.manor
    next_stage = max(1, int(profile.growth_stage) + 1)
    projection = _maintenance_projection_from_real_players(profile, rng=rng, config=config)
    target_building_level = next_stage
    target_guest_count = manor.guests.count()
    target_guest_level = next_stage
    if projection is not None:
        target_guest_count = min(max(target_guest_count + 1, 1), max(target_guest_count, int(projection.guest_count)))
        target_guest_level = min(max(next_stage, 1), max(next_stage, int(projection.guest_level)))

    _project_buildings(manor, level=target_building_level)
    _project_resources(manor, archetype=profile.archetype, rng=rng, config=config)
    projected_prestige = int(projection.prestige) if projection is not None else next_stage * 250
    gradual_prestige = min(max(int(manor.prestige or 0) + 250, target_building_level * 250), projected_prestige)
    manor.prestige = max(manor.prestige, gradual_prestige)
    manor.resource_updated_at = now
    manor.save(
        update_fields=["silver_capacity", "grain_capacity", "silver", "grain", "prestige", "resource_updated_at"]
    )
    _sync_profile_prestige_band(profile, config=config)
    _project_technologies(manor, level=max(1, target_building_level // 2), config=config)
    missing_guests = max(0, target_guest_count - manor.guests.count())
    if missing_guests:
        _project_guests_and_gear(
            manor,
            count=missing_guests,
            level=target_guest_level,
            rng=rng,
            config=config,
            archetype=str(profile.archetype),
        )
    for guest in manor.guests.all():
        if guest.level < target_guest_level:
            guest.level = target_guest_level
            guest.current_hp = guest.max_hp
            guest.save(update_fields=["level", "current_hp"])
        _grant_configured_extra_skills(guest, rng=rng, config=config)
    _project_troops(manor, count=max(50, target_building_level * 80), config=config)
    _replenish_inventory_stock(
        manor,
        level=max(1, target_building_level),
        rng=rng,
        config=config,
        archetype=str(profile.archetype),
        growth_stage=target_building_level,
        prestige=int(manor.prestige or 0),
        now=now,
    )

    profile.growth_stage = target_building_level
    profile.next_growth_at = _next_growth_time(now, profile, rng, config)
    profile.last_planned_at = now
    profile.save(update_fields=["growth_stage", "next_growth_at", "last_planned_at", "updated_at"])


def _loot_resource_total(loot_resources: Any) -> int:
    if not isinstance(loot_resources, dict):
        return 0
    total = 0
    for amount in loot_resources.values():
        try:
            total += max(0, int(amount or 0))
        except (TypeError, ValueError):
            continue
    return total


def _is_resource_empty(manor: Manor) -> bool:
    return int(manor.silver or 0) <= 0 and int(manor.grain or 0) <= 0


def _has_repeated_empty_raids(profile: BotProfile, *, now, config: dict[str, Any]) -> bool:
    lifecycle = config.get("lifecycle") or {}
    threshold = int(lifecycle.get("empty_hit_stale_threshold") or 0)
    if threshold <= 0 or not _is_resource_empty(profile.manor):
        return False
    window_hours = int(lifecycle.get("empty_hit_window_hours") or 24)
    since = now - timedelta(hours=max(1, window_hours))
    recent_loot = (
        RaidRun.objects.filter(
            defender=profile.manor,
            is_attacker_victory=True,
            started_at__gte=since,
        )
        .order_by("-started_at")
        .values_list("loot_resources", flat=True)[:threshold]
    )
    empty_hits = sum(1 for loot_resources in recent_loot if _loot_resource_total(loot_resources) <= 0)
    return empty_hits >= threshold


def _has_long_no_interaction(profile: BotProfile, *, now, config: dict[str, Any]) -> bool:
    lifecycle = config.get("lifecycle") or {}
    days = int(lifecycle.get("stale_no_interaction_days") or 0)
    if days <= 0:
        return False
    cutoff = now - timedelta(days=days)
    if profile.created_at > cutoff:
        return False
    return not (
        RaidRun.objects.filter(defender=profile.manor, started_at__gte=cutoff).exists()
        or ScoutRecord.objects.filter(defender=profile.manor, started_at__gte=cutoff).exists()
    )


def _mark_profile_stale(profile: BotProfile, *, now) -> None:
    profile.state = BotProfile.State.STALE
    profile.next_growth_at = now
    profile.save(update_fields=["state", "next_growth_at", "updated_at"])


def _maintain_profile(profile: BotProfile, *, now, config: dict[str, Any]) -> None:
    _sync_profile_prestige_band(profile, config=config)

    if profile.state == BotProfile.State.STALE:
        profile.state = BotProfile.State.RETIRED
        profile.maintenance_stopped_at = now
        profile.save(update_fields=["state", "maintenance_stopped_at", "updated_at"])
        return

    if _has_repeated_empty_raids(profile, now=now, config=config) or _has_long_no_interaction(
        profile, now=now, config=config
    ):
        _mark_profile_stale(profile, now=now)
        return

    if profile.retire_at <= now:
        _mark_profile_stale(profile, now=now)
        return

    if profile.abandon_at <= now:
        profile.state = BotProfile.State.ABANDONED
        profile.next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile.save(update_fields=["state", "next_growth_at", "updated_at"])
        return

    slowing_at = profile.abandon_at - max(timedelta(days=1), (profile.retire_at - profile.abandon_at) / 4)
    if profile.state == BotProfile.State.ACTIVE and slowing_at <= now:
        profile.state = BotProfile.State.SLOWING
        profile.next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile.last_planned_at = now
        profile.save(update_fields=["state", "next_growth_at", "last_planned_at", "updated_at"])
        return

    _maintain_active_profile(profile, now=now, config=config)


def maintain_due_virtual_players(*, now=None, limit: int = 100) -> int:
    now = now or timezone.now()
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return 0
    profile_ids = list(
        BotProfile.objects.exclude(state=BotProfile.State.RETIRED)
        .filter(next_growth_at__lte=now)
        .order_by("next_growth_at", "id")[: max(0, int(limit))]
        .values_list("id", flat=True)
    )
    maintained = 0
    for profile_id in profile_ids:
        with transaction.atomic():
            profile = (
                BotProfile.objects.select_for_update(skip_locked=True)
                .select_related("manor")
                .exclude(state=BotProfile.State.RETIRED)
                .filter(id=profile_id, next_growth_at__lte=now)
                .first()
            )
            if profile is None:
                continue
            _maintain_profile(profile, now=now, config=config)
            maintained += 1
    return maintained


def _retire_excess_virtual_players(
    *,
    target: int,
    now,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    target = max(0, int(target or 0))
    excess = _maintained_bot_count() - target
    if excess <= 0:
        return 0
    stale_ids = list(
        _maintained_bot_queryset()
        .filter(state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING, BotProfile.State.ABANDONED])
        .order_by("last_planned_at", "created_at", "id")
        .values_list("id", flat=True)[:excess]
    )
    if not stale_ids:
        return 0
    if ownership_guard is not None:
        ownership_guard()
    retired_count = BotProfile.objects.filter(id__in=stale_ids).update(state=BotProfile.State.STALE, next_growth_at=now)
    if retired_count > 0:
        logger.info(
            "Virtual player overpopulation retired: target=%s excess=%s retired_count=%s",
            target,
            excess,
            retired_count,
            extra={
                "event": "virtual_player_overpopulation_retired",
                "target": target,
                "excess": excess,
                "retired_count": retired_count,
            },
        )
    return retired_count


def plan_virtual_player_population(*, now=None) -> dict[str, Any]:
    config = load_virtual_player_config()
    now = now or timezone.now()
    active_real_players = _active_real_player_count(now)
    target_bot_total = _target_bot_total(config, now=now)
    return {
        "enabled": bool(config.get("enabled", True)),
        "regions": _regions(),
        "prestige_bands": list(_prestige_bands(config).keys()),
        "active_real_players": active_real_players,
        "target_bot_total": target_bot_total,
        "active_bots": _maintained_bot_count(),
        "planned_at": now.isoformat(),
    }


def _create_backfill_demanded_players(
    *,
    demands: list[dict[str, Any]],
    bands: dict[str, tuple[int, int | None]],
    hard_cap: int,
    limit: int,
    now,
    rng: random.Random,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    created = 0
    normalized_demands: list[dict[str, Any]] = []
    invalid_demand_ids: list[int] = []
    for demand in demands:
        demand_id = int(demand.get("id") or 0)
        band_name = str(demand.get("prestige_band") or "")
        region = str(demand.get("region") or "")
        needed = max(0, int(demand.get("needed") or 0))
        if band_name not in bands or region not in _regions() or needed <= 0:
            if demand_id > 0:
                invalid_demand_ids.append(demand_id)
            continue
        normalized_demands.append({"id": demand_id, "region": region, "prestige_band": band_name, "needed": needed})

    if invalid_demand_ids:
        if ownership_guard is not None:
            ownership_guard()
        BotBackfillDemand.objects.filter(id__in=invalid_demand_ids).delete()

    for demand in normalized_demands:
        if created >= limit:
            break
        demand_id = int(demand["id"])
        band_name = str(demand.get("prestige_band") or "")
        region = str(demand.get("region") or "")
        low, high = bands[band_name]
        needed = max(0, int(demand.get("needed") or 0))
        created_before_demand = created
        cap_reached = False
        while created < limit:
            seed = rng.randint(1, 2_147_483_647)
            if ownership_guard is not None:
                ownership_guard()
            with transaction.atomic():
                locked_demand = BotBackfillDemand.objects.select_for_update().filter(id=demand_id).first()
                if locked_demand is None or int(locked_demand.needed or 0) <= 0:
                    break
                current_active = _maintained_bot_count()
                if hard_cap > 0 and current_active >= hard_cap:
                    cap_reached = True
                    break
                if ownership_guard is not None:
                    ownership_guard()
                create_virtual_player(
                    region=region,
                    prestige_band=band_name,
                    archetype=_weighted_archetype(rng),
                    growth_seed=seed,
                    now=now,
                    projection=_projection_for_band(band_name, low, high, rng, region=region),
                )
                locked_demand.needed = max(0, int(locked_demand.needed or 0) - 1)
                if locked_demand.needed <= 0:
                    locked_demand.delete()
                else:
                    locked_demand.save(update_fields=["needed", "updated_at"])
            created += 1
        if needed > 0:
            created_for_demand = created - created_before_demand
            logger.info(
                "Virtual player backfill demand consumed: region=%s prestige_band=%s created=%s needed=%s",
                region,
                band_name,
                created_for_demand,
                needed,
                extra={
                    "event": "virtual_player_backfill_demand_consumed",
                    "region": region,
                    "prestige_band": band_name,
                    "created_count": created_for_demand,
                    "needed": needed,
                },
            )
        if cap_reached or created >= limit:
            return created
    return created


def _roll_population_deficits(
    *,
    bands: dict[str, tuple[int, int | None]],
    min_per_region: int,
    min_per_band: int,
    target_bot_total: int,
    active_bot_count: int,
    limit: int,
    now,
    rng: random.Random,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    planned_by_region = {region: 0 for region in _regions()}
    total_planned = 0
    for region in _regions():
        for band_name, (low, high) in bands.items():
            existing = (
                _maintained_bot_queryset()
                .filter(
                    _target_band_filter(band_name),
                    manor__region=region,
                )
                .count()
            )
            real_active = Manor.objects.filter(
                bot_profile__isnull=True,
                user__is_staff=False,
                user__is_superuser=False,
                region=region,
                last_active_at__gte=now - timedelta(days=7),
                **_band_filter_kwargs(low, high),
            ).count()
            deficit = max(0, min_per_band - existing)
            cells.append(
                {
                    "region": region,
                    "band_name": band_name,
                    "low": low,
                    "high": high,
                    "existing": existing,
                    "real_active": real_active,
                    "deficit": deficit,
                }
            )
            planned_by_region[region] += deficit
            total_planned += deficit

    for region in _regions():
        current = int(_maintained_bot_queryset().filter(manor__region=region).count())
        missing = max(0, min_per_region - current - planned_by_region[region])
        if missing <= 0:
            continue
        regional_cells = [cell for cell in cells if cell["region"] == region]
        regional_cells.sort(key=lambda cell: (int(cell["existing"]) + int(cell["deficit"]), str(cell["band_name"])))
        while missing > 0 and regional_cells:
            for cell in regional_cells:
                if missing <= 0:
                    break
                cell["deficit"] += 1
                planned_by_region[region] += 1
                total_planned += 1
                missing -= 1

    global_missing = max(0, target_bot_total - active_bot_count - total_planned)
    shuffled_cells = list(cells)
    tie_breakers = {id(cell): rng.random() for cell in shuffled_cells}
    projected_region_totals = {
        region: _maintained_bot_queryset().filter(manor__region=region).count() + planned_by_region[region]
        for region in _regions()
    }
    projected_band_totals = {
        band_name: sum(int(cell["existing"]) + int(cell["deficit"]) for cell in cells if cell["band_name"] == band_name)
        for band_name in bands
    }
    while global_missing > 0 and shuffled_cells and total_planned < limit:
        cell = min(
            shuffled_cells,
            key=lambda row: (
                0 if int(row["existing"]) + int(row["deficit"]) < int(row["real_active"]) else 1,
                projected_region_totals[str(row["region"])],
                projected_band_totals[str(row["band_name"])],
                int(row["existing"]) + int(row["deficit"]),
                tie_breakers[id(row)],
            ),
        )
        cell["deficit"] += 1
        projected_region_totals[str(cell["region"])] += 1
        projected_band_totals[str(cell["band_name"])] += 1
        total_planned += 1
        global_missing -= 1

    return [cell for cell in shuffled_cells if int(cell["deficit"]) > 0]


def roll_virtual_player_population(*, limit: int | None = None, now=None) -> int:
    acquired, from_cache, lock_token = acquire_best_effort_lock(
        ROLL_LOCK_KEY,
        timeout_seconds=ROLL_LOCK_TIMEOUT_SECONDS,
        logger=logger,
        log_context="virtual player population roll",
        allow_local_fallback=False,
    )
    if not acquired:
        return 0

    stop_heartbeat = Event()
    lost_ownership = Event()
    heartbeat_failed = Event()
    heartbeat_errors: list[Exception] = []

    def _ownership_guard() -> None:
        if heartbeat_failed.is_set():
            raise heartbeat_errors[0]
        if not lost_ownership.is_set():
            return
        raise VirtualPlayerPopulationLockLostError("virtual player population roll lock ownership was lost")

    def _heartbeat() -> None:
        interval_seconds = max(1, int(ROLL_LOCK_TIMEOUT_SECONDS)) / 3
        try:
            while not stop_heartbeat.wait(interval_seconds):
                renewed = renew_best_effort_lock(
                    ROLL_LOCK_KEY,
                    from_cache=from_cache,
                    lock_token=lock_token,
                    timeout_seconds=ROLL_LOCK_TIMEOUT_SECONDS,
                    logger=logger,
                    log_context="virtual player population roll",
                )
                if not renewed:
                    lost_ownership.set()
                    stop_heartbeat.set()
                    return
        except Exception as exc:
            heartbeat_errors.append(exc)
            heartbeat_failed.set()
            stop_heartbeat.set()
            logger.exception("Virtual player population roll heartbeat raised an unexpected error")

    heartbeat = Thread(target=_heartbeat, name="virtual-player-population-lock-heartbeat", daemon=True)
    heartbeat_started = False
    try:
        heartbeat.start()
        heartbeat_started = True
        result = _roll_virtual_player_population_unlocked(
            limit=limit,
            now=now,
            ownership_guard=_ownership_guard,
        )
        stop_heartbeat.set()
        heartbeat.join()
        _ownership_guard()
        return result
    finally:
        stop_heartbeat.set()
        if heartbeat_started and heartbeat.is_alive():
            heartbeat.join()
        release_best_effort_lock(
            ROLL_LOCK_KEY,
            from_cache=from_cache,
            lock_token=lock_token,
            logger=logger,
            log_context="virtual player population roll",
        )


def _roll_virtual_player_population_unlocked(
    *,
    limit: int | None = None,
    now=None,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return 0

    now = now or timezone.now()
    population = config.get("population") or {}
    hard_cap = int(population.get("hard_cap", 0) or 0)
    min_per_region = max(0, int(population.get("min_per_region", 0) or 0))
    min_per_band = max(0, int(population.get("min_attackable_per_band", 0) or 0))
    bands = _prestige_bands(config)
    target_bot_total = _target_bot_total(config, now=now)
    minimum_bot_total = max(
        target_bot_total,
        len(_regions()) * min_per_region,
        len(_regions()) * len(bands) * min_per_band,
    )
    target_bot_total = minimum_bot_total
    target_limit = min(target_bot_total, hard_cap) if hard_cap > 0 else target_bot_total
    retired_for_capacity = _retire_excess_virtual_players(
        target=target_limit,
        now=now,
        ownership_guard=ownership_guard,
    )
    active_bot_count = _maintained_bot_count()
    if hard_cap > 0 and active_bot_count >= hard_cap:
        return 0

    rng = random.Random(int(now.timestamp()))
    if limit is None:
        limit = _range_value(rng, population.get("rolling_batch_size"), default=(3, 12))
    limit = max(0, int(limit))
    if limit <= 0:
        return 0

    if not bands:
        return retired_for_capacity

    created = _create_backfill_demanded_players(
        demands=[
            dict(row)
            for row in BotBackfillDemand.objects.order_by("region", "prestige_band", "id").values(
                "id",
                "region",
                "prestige_band",
                "needed",
            )[:limit]
        ],
        bands=bands,
        hard_cap=hard_cap,
        limit=limit,
        now=now,
        rng=rng,
        ownership_guard=ownership_guard,
    )

    active_bot_count = _maintained_bot_count()
    remaining_limit = max(0, limit - created)
    deficit_cells = _roll_population_deficits(
        bands=bands,
        min_per_region=min_per_region,
        min_per_band=min_per_band,
        target_bot_total=target_bot_total,
        active_bot_count=active_bot_count,
        limit=remaining_limit,
        now=now,
        rng=rng,
    )
    while created < limit and deficit_cells:
        progressed = False
        for cell in deficit_cells:
            if created >= limit:
                break
            if int(cell["deficit"]) <= 0:
                continue
            current_active = _maintained_bot_count()
            if hard_cap > 0 and current_active >= hard_cap:
                return created
            seed = rng.randint(1, 2_147_483_647)
            if ownership_guard is not None:
                ownership_guard()
            create_virtual_player(
                region=str(cell["region"]),
                prestige_band=str(cell["band_name"]),
                archetype=_weighted_archetype(rng),
                growth_seed=seed,
                now=now,
                projection=_projection_for_band(
                    str(cell["band_name"]),
                    int(cell["low"]),
                    cell["high"],
                    rng,
                    region=str(cell["region"]),
                ),
            )
            cell["deficit"] = int(cell["deficit"]) - 1
            created += 1
            progressed = True
        if not progressed:
            break
        deficit_cells = [cell for cell in deficit_cells if int(cell["deficit"]) > 0]
    return created
