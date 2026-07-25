from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from functools import lru_cache
from hashlib import blake2b, sha256
from pathlib import Path
from threading import Event, Thread
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from battle.models import TroopTemplate
from common.constants.virtual_players import VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES
from core.config import GUEST
from core.exceptions import InsufficientResourceError, NoGuestsError, SalaryAlreadyPaidError
from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock, renew_best_effort_lock
from core.utils.yaml_loader import load_yaml_data
from gameplay.constants import REGION_CHOICES, BuildingKeys, PVPConstants
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaVirtualReserveMember,
    BotBackfillDemand,
    BotInventoryDailyCounter,
    BotPopulationControl,
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
from gameplay.services.virtual_player_population import PopulationCell, PopulationPlan, plan_population_cells
from gameplay.services.virtual_player_rules import (
    apply_combat_persona,
    apply_stable_troop_variation,
    bounded_approach,
    choose_lifecycle,
    choose_strength_quantile,
    nearest_rank_quantile,
)
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestRarity,
    GuestSkill,
    GuestTemplate,
    Skill,
    SkillKind,
)
from guests.services.equipment_payloads import build_gear_template_defaults, build_gear_template_preview
from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest, slot_capacity
from guests.services.recruitment_guests import create_guest_from_template
from guests.services.salary import pay_all_salaries

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotProjectionConfig:
    prestige: int
    building_level: int
    guest_count: int
    guest_level: int
    troop_count: int = 50


class PopulationMutationStatus(str, Enum):
    CREATED = "created"
    REACTIVATED = "reactivated"
    CAP_REACHED = "cap_reached"
    UNAVAILABLE = "unavailable"


class AcceleratedGrowthOutcome(str, Enum):
    GROWN = "grown"
    BUSY = "busy"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class PopulationMutationResult:
    status: PopulationMutationStatus
    profile: BotProfile | None
    hard_cap: int
    maintained_count: int


DEFAULT_VIRTUAL_PLAYER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "population": {
        "active_window_days": 7,
        "region_floor": 8,
        "region_active_multiplier": 8,
        "global_floor": 32,
        "global_active_multiplier": 20,
        "exploration_supply": 0,
        "min_attackable_per_band": 4,
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
    "growth": {
        "catch_up_ratio": 0.25,
        "slowing_ratio_multiplier": 0.5,
        "max_building_step": 2,
        "max_guest_level_step": 3,
        "max_prestige_step": 500,
        "stage_caps": {
            "newbie": 3,
            "junior": 6,
            "middle": 10,
            "senior": 15,
            "veteran": 20,
        },
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
        "early_stage_skill_max": 6,
        "early_stage_skill_count": [0, 1],
        "multi_skill_passive_focus_chance": 0.75,
        "troop_template_keys": [],
        "technology_keys": [],
        "gear_max_rarity_by_stage": {
            1: "green",
            7: "blue",
            11: "purple",
            16: "orange",
        },
        "real_projection_sample_size": 25,
        "active_sample_days": 30,
        "regional_min_sample_size": 5,
        "strength_quantile_weights": {"p25": 25, "p50": 50, "p75": 25},
        "real_projection_jitter_bps": 500,
        "inventory_template_slots_by_archetype": {
            "balanced": 4,
            "rich": 5,
            "dojo": 3,
            "guard": 3,
            "abandoned": 4,
        },
        "inventory_effect_type_weights": {
            "balanced": {"resource_pack": 3, "resource": 3, "experience_items": 2, "medicine": 2, "tool": 1},
            "rich": {"resource_pack": 4, "resource": 5, "experience_items": 1, "medicine": 1, "tool": 1},
            "dojo": {"resource_pack": 1, "resource": 1, "experience_items": 4, "medicine": 2, "tool": 1},
            "guard": {"resource_pack": 2, "resource": 2, "experience_items": 1, "medicine": 4, "tool": 1},
            "abandoned": {"resource_pack": 3, "resource": 3, "experience_items": 1, "medicine": 1, "tool": 1},
        },
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
    "combat_personas": {
        "balanced": {"guest_level_multiplier": 1.0, "guest_count_multiplier": 1.0, "troop_multiplier": 1.0},
        "rich": {"guest_level_multiplier": 0.85, "guest_count_multiplier": 0.85, "troop_multiplier": 0.8},
        "dojo": {"guest_level_multiplier": 1.15, "guest_count_multiplier": 1.0, "troop_multiplier": 0.75},
        "guard": {"guest_level_multiplier": 0.85, "guest_count_multiplier": 0.85, "troop_multiplier": 1.35},
        "abandoned": {"guest_level_multiplier": 0.75, "guest_count_multiplier": 0.75, "troop_multiplier": 0.6},
    },
    "lifecycle_personas": {
        "tourist": {"weight": 15, "active_days": [7, 21], "abandoned_days": [7, 14]},
        "casual": {"weight": 45, "active_days": [30, 90], "abandoned_days": [14, 45]},
        "committed": {"weight": 30, "active_days": [90, 180], "abandoned_days": [30, 60]},
        "veteran": {"weight": 10, "active_days": [180, 360], "abandoned_days": [45, 90]},
    },
}

VIRTUAL_PLAYER_CONFIG_PATH = Path(settings.BASE_DIR) / "data" / "virtual_players.yaml"
ROLL_LOCK_KEY = "virtual_players:roll_lock"
ROLL_LOCK_TIMEOUT_SECONDS = 300
VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT = 5
RARE_ITEM_RARITIES = {"purple", "orange", "red", "legendary"}
ALL_TEMPLATE_SENTINEL = "__all__"
ALL_TRADEABLE_TEMPLATE_SENTINEL = "__all_tradeable__"
GEAR_RARITY_RANK = {rarity.value: index for index, rarity in enumerate(GuestRarity)}

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
    "周末上线",
    "慢慢变强",
    "路过看看",
    "今日份",
    "刚睡醒",
    "再来一局",
    "不急着赢",
    "下班以后",
    "在线等风",
)
_MANOR_NAME_INTERNET_SUFFIXES = (
    "山庄",
    "小筑",
    "根据地",
    "休息区",
    "补给站",
    "快乐屋",
    "慢慢来",
    "先发育",
    "不掉线",
    "来收菜",
    "等好运",
    "随便玩",
    "今晚在线",
    "明天再说",
    "营业中",
    "集合点",
    "避风港",
    "后花园",
)
_MANOR_NAME_INTERNET_STANDALONE = (
    "听到涛声",
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
    "等等我再上",
    "今天手气不错",
    "先把日常做完",
    "晚点再认真打",
    "刚来还不太会",
    "慢慢玩比较快",
    "让我再发育会儿",
    "路过顺手收个菜",
    "上线看看就走",
    "今天不宜硬刚",
    "差一点点起飞",
    "先喝口茶再说",
    "周末才有空玩",
    "等一个好运气",
    "随手点进来的",
    "别急正在赶路",
    "这一局先稳住",
    "明天一定变强",
)
_MANOR_NAME_NICKNAME_STANDALONE = (
    "晚风",
    "南桥",
    "半糖",
    "小满",
    "十一",
    "阿七",
    "木棉",
    "青团",
    "栗子",
    "乌龙",
    "夏末",
    "星河",
    "山茶",
    "初九",
    "清欢",
    "玖玖",
    "北落",
    "三月",
    "白桃",
    "雾眠",
    "小禾",
    "小雨",
    "阿宁",
    "团子",
    "慢热",
    "未晚",
    "一川",
    "听夏",
    "有光",
    "云朵",
    "小鱼干",
    "松子糖",
)
_MANOR_NAME_NICKNAME_PREFIXES = (
    "小",
    "阿",
    "老",
    "一只",
    "隔壁的",
    "晚睡的",
    "路过的",
    "发呆的",
    "爱喝茶的",
    "慢半拍的",
    "不着急的",
    "刚上线的",
)
_MANOR_NAME_NICKNAME_CORES = (
    "栗子",
    "青团",
    "乌龙",
    "晚风",
    "小禾",
    "山茶",
    "木棉",
    "团子",
    "南桥",
    "白桃",
    "云朵",
    "星河",
    "小满",
    "听夏",
    "雨声",
    "松子",
    "月亮",
    "茶壶",
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


def _select_bot_manor_name_style(roll: float) -> str:
    if roll < 0.50:
        return "modern"
    if roll < 0.80:
        return "classical"
    return "nickname"


def _build_bot_manor_name_candidate(rng: random.Random, *, style: str, variant: int) -> str:
    if style == "modern":
        if rng.random() < 0.30:
            return rng.choice(_MANOR_NAME_INTERNET_STANDALONE)
        return f"{rng.choice(_MANOR_NAME_INTERNET_PREFIXES)}{rng.choice(_MANOR_NAME_INTERNET_SUFFIXES)}"
    if style == "nickname":
        if rng.random() < 0.50:
            return rng.choice(_MANOR_NAME_NICKNAME_STANDALONE)
        return f"{rng.choice(_MANOR_NAME_NICKNAME_PREFIXES)}{rng.choice(_MANOR_NAME_NICKNAME_CORES)}"
    if style != "classical":
        raise ValueError(f"Unsupported bot manor name style: {style}")

    classical_variant = variant % 5
    if classical_variant == 0:
        return f"{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_GIVEN)}的庄园"
    if classical_variant == 1:
        return f"{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_GIVEN)}的{rng.choice(_MANOR_NAME_SUFFIXES)}"
    if classical_variant == 2:
        return f"{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_SURNAMES)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
    if classical_variant == 3:
        return f"{rng.choice(_MANOR_NAME_GIVEN)}{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_SUFFIXES)}"
    return f"{rng.choice(_MANOR_NAME_PREFIXES)}{rng.choice(_MANOR_NAME_GIVEN)}{rng.choice(_MANOR_NAME_SUFFIXES)}"


def _generate_bot_manor_name(*, growth_seed: int, salt: int = 0) -> str:
    """Generate player-like manor names without visible system markers."""
    for attempt in range(400):
        rng = random.Random(f"{growth_seed}:{salt}:{attempt}")
        style = _select_bot_manor_name_style(rng.random())
        candidate = _build_bot_manor_name_candidate(rng, style=style, variant=attempt)
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


def _population_cell_membership_filter(
    prestige_band: str,
    *,
    config: dict[str, Any],
    target_based: bool,
) -> Q:
    if target_based:
        return _target_band_filter(prestige_band)
    low, high = _prestige_bands(config)[prestige_band]
    return Q(**_band_filter_kwargs(low, high, prefix="manor__"))


def record_virtual_player_backfill_demand(*, region: str, prestige_band: str, needed: int) -> None:
    """Reconcile the current async bot backfill shortage for one population cell."""
    needed = max(0, int(needed or 0))
    if not region or not prestige_band:
        return
    normalized_region = str(region)
    normalized_band = str(prestige_band)
    with transaction.atomic():
        demand = (
            BotBackfillDemand.objects.select_for_update()
            .filter(
                region=normalized_region,
                prestige_band=normalized_band,
            )
            .first()
        )
        if needed <= 0:
            if demand is not None:
                demand.delete()
            return
        if demand is None:
            BotBackfillDemand.objects.select_for_update().update_or_create(
                region=normalized_region,
                prestige_band=normalized_band,
                defaults={"needed": needed},
            )
        elif needed != int(demand.needed or 0):
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


def _should_reactivate_retired_player(
    *,
    now,
    region: str,
    prestige_band: str,
    profile_id: int,
    chance: float,
) -> bool:
    normalized_chance = max(0.0, min(1.0, float(chance)))
    if normalized_chance <= 0:
        return False
    if normalized_chance >= 1:
        return True
    local_date = timezone.localtime(now).date() if timezone.is_aware(now) else now.date()
    payload = f"{local_date.isoformat()}:{region}:{prestige_band}:{int(profile_id)}".encode("utf-8")
    value = int.from_bytes(sha256(payload).digest()[:8], "big") / 2**64
    return value < normalized_chance


def _reactivate_locked_virtual_player_profile(profile: BotProfile, *, now) -> BotProfile:
    current_time = now
    local_date = timezone.localtime(current_time).date() if timezone.is_aware(current_time) else current_time.date()
    config = load_virtual_player_config()
    lifecycle_rng = random.Random(f"reactivate:{local_date.isoformat()}:{profile.id}")
    _next_growth_at, abandon_at, retire_at = _lifecycle_dates(current_time, lifecycle_rng, config)
    profile.state = BotProfile.State.ACTIVE
    profile.next_growth_at = current_time
    profile.abandon_at = abandon_at
    profile.retire_at = retire_at
    profile.maintenance_started_at = current_time
    profile.maintenance_stopped_at = None
    profile.last_planned_at = current_time
    profile.save(
        update_fields=[
            "state",
            "next_growth_at",
            "abandon_at",
            "retire_at",
            "maintenance_started_at",
            "maintenance_stopped_at",
            "last_planned_at",
            "updated_at",
        ]
    )
    logger.info(
        "Virtual player reactivated: profile_id=%s manor_id=%s region=%s prestige_band=%s",
        profile.id,
        profile.manor_id,
        profile.manor.region,
        _profile_target_prestige_band(profile),
        extra={
            "event": "virtual_player_reactivated",
            "profile_id": profile.id,
            "manor_id": profile.manor_id,
            "region": profile.manor.region,
            "current_prestige_band": profile.current_prestige_band,
            "target_prestige_band": _profile_target_prestige_band(profile),
        },
    )
    return profile


@transaction.atomic
def reactivate_retired_virtual_player_with_capacity(
    profile_id: int,
    *,
    now=None,
) -> PopulationMutationResult:
    current_time = now or timezone.now()
    hard_cap, maintained_count = _lock_population_capacity(now=current_time)
    profile = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(pk=profile_id, state=BotProfile.State.RETIRED)
        .first()
    )
    if profile is None:
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    if not _population_has_room(hard_cap, maintained_count):
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    reactivated = _reactivate_locked_virtual_player_profile(profile, now=current_time)
    return PopulationMutationResult(
        status=PopulationMutationStatus.REACTIVATED,
        profile=reactivated,
        hard_cap=hard_cap,
        maintained_count=maintained_count,
    )


@transaction.atomic
def reactivate_virtual_player_profile(profile_id: int, *, now=None) -> BotProfile | None:
    current_time = now or timezone.now()
    state = BotProfile.objects.filter(pk=profile_id).values_list("state", flat=True).first()
    if state == BotProfile.State.RETIRED:
        return reactivate_retired_virtual_player_with_capacity(
            profile_id,
            now=current_time,
        ).profile

    profile = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(pk=profile_id, state=BotProfile.State.ABANDONED)
        .first()
    )
    if profile is None:
        return None
    return _reactivate_locked_virtual_player_profile(profile, now=current_time)


@transaction.atomic
def _try_reactivate_retired_player(
    *,
    region: str,
    prestige_band: str,
    low: int,
    high: int | None,
    now,
    config: dict[str, Any],
    evaluated_profile_ids: set[int],
    ownership_guard: Callable[[], None] | None = None,
) -> BotProfile | None:
    if ownership_guard is not None:
        ownership_guard()
    hard_cap, maintained_count = _lock_population_capacity(now=now)
    if not _population_has_room(hard_cap, maintained_count):
        return None
    queryset = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            _population_cell_membership_filter(
                prestige_band,
                config=config,
                target_based=_uses_regional_population_planning(),
            )
        )
        .filter(
            state=BotProfile.State.RETIRED,
            manor__region=str(region),
        )
        .exclude(id__in=evaluated_profile_ids)
        .order_by("-maintenance_stopped_at", "-updated_at", "id")
    )
    profile = queryset.first()
    if profile is None:
        return None
    evaluated_profile_ids.add(int(profile.id))
    if ownership_guard is not None:
        ownership_guard()
    return _reactivate_locked_virtual_player_profile(profile, now=now)


@transaction.atomic
def _reactivate_or_create_virtual_player(
    *,
    region: str,
    prestige_band: str,
    low: int,
    high: int | None,
    archetype: str,
    growth_seed: int,
    now,
    config: dict[str, Any],
    projection_factory: Callable[[], BotProjectionConfig],
    evaluated_profile_ids: set[int],
    ownership_guard: Callable[[], None] | None = None,
    require_population_deficit: bool = False,
    include_target_pipeline: bool = False,
) -> PopulationMutationResult:
    if ownership_guard is not None:
        ownership_guard()
    hard_cap, maintained_count = _lock_population_capacity(now=now)
    if not _population_has_room(hard_cap, maintained_count):
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )
    if require_population_deficit:
        current_cell = _build_population_plan(config, now=now).by_key.get((str(region), str(prestige_band)))
        current_deficit = 0 if current_cell is None else current_cell.structural_deficit
        if current_cell is not None and include_target_pipeline:
            current_band_filter = Q(**_band_filter_kwargs(low, high, prefix="manor__"))
            pipeline_supply = (
                _maintained_bot_queryset()
                .filter(manor__region=str(region))
                .filter(_target_band_filter(prestige_band) | current_band_filter)
                .count()
            )
            current_deficit = max(0, int(current_cell.target) - pipeline_supply)
        if current_deficit <= 0:
            return PopulationMutationResult(
                status=PopulationMutationStatus.UNAVAILABLE,
                profile=None,
                hard_cap=hard_cap,
                maintained_count=maintained_count,
            )

    retired = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            _population_cell_membership_filter(
                prestige_band,
                config=config,
                target_based=_uses_regional_population_planning(),
            )
        )
        .filter(
            state=BotProfile.State.RETIRED,
            manor__region=str(region),
        )
        .exclude(id__in=evaluated_profile_ids)
        .order_by("-maintenance_stopped_at", "-updated_at", "id")
        .first()
    )
    if retired is not None:
        evaluated_profile_ids.add(int(retired.id))
        if ownership_guard is not None:
            ownership_guard()
        reactivated = _reactivate_locked_virtual_player_profile(retired, now=now)
        return PopulationMutationResult(
            status=PopulationMutationStatus.REACTIVATED,
            profile=reactivated,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )

    if ownership_guard is not None:
        ownership_guard()
    profile = create_virtual_player(
        region=region,
        prestige_band=prestige_band,
        archetype=archetype,
        growth_seed=growth_seed,
        now=now,
        projection=projection_factory(),
        start_from_zero=True,
    )
    return PopulationMutationResult(
        status=PopulationMutationStatus.CREATED,
        profile=profile,
        hard_cap=hard_cap,
        maintained_count=maintained_count,
    )


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


def get_virtual_player_backfill_search_limit() -> int:
    """Return the attackable-target threshold needed by a region search."""
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return 0
    population = config.get("population") or {}
    return max(0, int(population.get("min_attackable_per_band", 0) or 0))


def request_virtual_player_backfill_for_region_search(*, searcher: Manor, region: str) -> bool:
    """Record an explicit region-search shortage for a later population roll."""
    if region not in _regions():
        return False
    candidate_limit = get_virtual_player_backfill_search_limit()
    if candidate_limit <= 0:
        return False
    if searcher.is_under_newbie_protection or searcher.is_under_peace_shield:
        return False

    from gameplay.services.raid.map_search import count_attackable_manors_by_region

    candidate_count = count_attackable_manors_by_region(
        searcher,
        region,
        limit=candidate_limit,
    )
    record_virtual_player_backfill_demand_for_search(
        searcher=searcher,
        region=region,
        candidate_count=candidate_count,
    )
    return True


def _active_real_player_count(now) -> int:
    config = load_virtual_player_config()
    active_days = max(1, int((config.get("population") or {}).get("active_window_days") or 7))
    active_after = now - timedelta(days=active_days)
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


def _arena_protected_bot_manor_ids() -> set[int]:
    protected = set(
        ArenaEntry.objects.filter(
            status=ArenaEntry.Status.REGISTERED,
            tournament__status__in=[
                ArenaTournament.Status.RECRUITING,
                ArenaTournament.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    protected.update(
        ArenaCoopEntry.objects.filter(
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    return protected


def _configured_population_value(
    population: dict[str, Any],
    field: str,
    *,
    legacy_field: str,
    default: int,
) -> int:
    runtime = getattr(settings, "VIRTUAL_PLAYER_CONFIG", None) or {}
    runtime_population = runtime.get("population") if isinstance(runtime, dict) else None
    if isinstance(runtime_population, dict):
        if field in runtime_population:
            return int(runtime_population[field] or 0)
        if legacy_field in runtime_population:
            return int(runtime_population[legacy_field] or 0)
    return int(population.get(field, population.get(legacy_field, default)) or 0)


def _uses_regional_population_planning() -> bool:
    runtime = getattr(settings, "VIRTUAL_PLAYER_CONFIG", None) or {}
    runtime_population = runtime.get("population") if isinstance(runtime, dict) else None
    if not isinstance(runtime_population, dict) or not runtime_population:
        return True
    regional_fields = {
        "region_floor",
        "region_active_multiplier",
        "global_floor",
        "global_active_multiplier",
    }
    if regional_fields.intersection(runtime_population):
        return True
    legacy_planning_fields = {
        "active_player_multiplier",
        "cell_floor",
        "cell_active_multiplier",
        "min_per_region",
    }
    return not bool(legacy_planning_fields.intersection(runtime_population))


def _population_config_int(population: dict[str, Any], field: str, default: int) -> int:
    value = population.get(field, default)
    return int(default if value is None else value)


def _build_population_plan(config: dict[str, Any], *, now) -> PopulationPlan:
    population = config.get("population") or {}
    uses_regional_planning = _uses_regional_population_planning()
    active_days = max(1, int(population.get("active_window_days") or 7))
    active_after = now - timedelta(days=active_days)
    recent_after = now - timedelta(hours=24)
    exhausted_manor_ids = list(
        RaidRun.objects.filter(started_at__gte=recent_after, defender__bot_profile__isnull=False)
        .values("defender_id")
        .annotate(received=Count("id"))
        .filter(received__gte=PVPConstants.RAID_MAX_DAILY_ATTACKS_RECEIVED)
        .values_list("defender_id", flat=True)
    )
    maintained = _maintained_bot_queryset().select_related("manor")
    attackable = maintained.filter(
        Q(manor__newbie_protection_until__isnull=True) | Q(manor__newbie_protection_until__lte=now),
        Q(manor__defeat_protection_until__isnull=True) | Q(manor__defeat_protection_until__lte=now),
        Q(manor__peace_shield_until__isnull=True) | Q(manor__peace_shield_until__lte=now),
    ).exclude(manor_id__in=exhausted_manor_ids)
    demands = {
        (row["region"], row["prestige_band"]): int(row["needed"] or 0)
        for row in BotBackfillDemand.objects.values("region", "prestige_band", "needed")
    }

    cells: list[PopulationCell] = []
    for region in _regions():
        for band_name, (low, high) in _prestige_bands(config).items():
            band_filter = _band_filter_kwargs(low, high, prefix="manor__")
            real_filter = _band_filter_kwargs(low, high)
            cells.append(
                PopulationCell(
                    region=region,
                    prestige_band=band_name,
                    active_real=Manor.objects.filter(
                        bot_profile__isnull=True,
                        user__is_staff=False,
                        user__is_superuser=False,
                        region=region,
                        last_active_at__gte=active_after,
                        **real_filter,
                    ).count(),
                    maintained_supply=maintained.filter(manor__region=region)
                    .filter(
                        _population_cell_membership_filter(
                            band_name,
                            config=config,
                            target_based=uses_regional_planning,
                        )
                    )
                    .count(),
                    attackable_supply=attackable.filter(manor__region=region, **band_filter).count(),
                    search_demand=demands.get((region, band_name), 0),
                )
            )

    if uses_regional_planning:
        entry_band = _prestige_band_for_value(0, config) or next(iter(_prestige_bands(config)), "newbie")
        hard_cap_override = int(population.get("hard_cap") or 0) if "hard_cap" in population else None
        return plan_population_cells(
            cells,
            region_floor=max(0, _population_config_int(population, "region_floor", 8)),
            region_multiplier=max(0, _population_config_int(population, "region_active_multiplier", 8)),
            global_floor=max(0, _population_config_int(population, "global_floor", 32)),
            global_multiplier=max(0, _population_config_int(population, "global_active_multiplier", 20)),
            entry_band=entry_band,
            hard_cap_override=hard_cap_override,
        )

    return plan_population_cells(
        cells,
        cell_floor=max(
            0,
            _configured_population_value(
                population,
                "cell_floor",
                legacy_field="min_attackable_per_band",
                default=4,
            ),
        ),
        cell_multiplier=max(
            0,
            _configured_population_value(
                population,
                "cell_active_multiplier",
                legacy_field="active_player_multiplier",
                default=2,
            ),
        ),
        exploration_supply=max(0, int(population.get("exploration_supply") or 0)),
        hard_cap=max(0, int(population.get("hard_cap") or 0)),
    )


def get_virtual_player_capacity(*, now=None) -> tuple[int, int]:
    current_time = now or timezone.now()
    population_plan = _build_population_plan(load_virtual_player_config(), now=current_time)
    return population_plan.hard_cap, _maintained_bot_count()


def _select_virtual_player_creation_region(*, now) -> str | None:
    population_plan = _build_population_plan(load_virtual_player_config(), now=now)
    region_targets = population_plan.region_targets
    if not region_targets:
        return None
    maintained_by_region = {
        str(row["manor__region"]): int(row["count"] or 0)
        for row in _maintained_bot_queryset().values("manor__region").annotate(count=Count("id"))
    }
    return min(
        region_targets,
        key=lambda region: (
            -(int(region_targets[region]) - maintained_by_region.get(region, 0)),
            region,
        ),
    )


def _lock_population_capacity(*, now) -> tuple[int, int]:
    BotPopulationControl.objects.select_for_update().get_or_create(
        key=BotPopulationControl.GLOBAL_KEY,
    )
    return get_virtual_player_capacity(now=now)


def _population_has_room(hard_cap: int, maintained_count: int) -> bool:
    return hard_cap <= 0 or maintained_count < hard_cap


def rebalance_virtual_player_target_bands(population_plan: PopulationPlan, *, limit: int) -> int:
    remaining = max(0, int(limit))
    updated = 0
    protected_manor_ids = _arena_protected_bot_manor_ids()
    for region in sorted(population_plan.region_targets):
        desired = {cell.prestige_band: cell.target for cell in population_plan.cells if cell.region == region}
        current = {
            band: _maintained_bot_queryset().filter(manor__region=region).filter(_target_band_filter(band)).count()
            for band in desired
        }
        deficits = [band for band in desired if desired[band] > current.get(band, 0)]
        for target_band in sorted(
            deficits,
            key=lambda band: (-(desired[band] - current.get(band, 0)), band),
        ):
            needed = desired[target_band] - current.get(target_band, 0)
            donor_bands = [band for band in desired if current.get(band, 0) > desired[band]]
            for donor_band in sorted(donor_bands):
                if remaining <= 0 or needed <= 0:
                    return updated
                with transaction.atomic():
                    profile_ids = list(
                        _maintained_bot_queryset()
                        .select_for_update(skip_locked=True)
                        .filter(
                            manor__region=region,
                            arena_virtual_reserve__isnull=True,
                        )
                        .exclude(manor_id__in=protected_manor_ids)
                        .filter(_target_band_filter(donor_band))
                        .order_by("last_planned_at", "id")
                        .values_list("id", flat=True)[: min(remaining, needed)]
                    )
                    changed = (
                        _maintained_bot_queryset()
                        .filter(
                            id__in=profile_ids,
                            manor__region=region,
                            arena_virtual_reserve__isnull=True,
                        )
                        .exclude(manor_id__in=_arena_protected_bot_manor_ids())
                        .filter(_target_band_filter(donor_band))
                        .update(
                            target_prestige_band=target_band,
                            prestige_band=target_band,
                        )
                    )
                updated += changed
                remaining -= changed
                needed -= changed
                current[donor_band] -= changed
                current[target_band] = current.get(target_band, 0) + changed
    return updated


def _projection_from_real_players(
    *,
    region: str | None,
    low: int,
    high: int | None,
    rng: random.Random,
    config: dict[str, Any] | None = None,
    now=None,
    sample_seed: int | None = None,
    strength_quantile: str = "p50",
) -> BotProjectionConfig | None:
    config = config or load_virtual_player_config()
    now = now or timezone.now()
    projection_config = config.get("projection") or {}
    active_sample_days = max(1, int(projection_config.get("active_sample_days") or 30))
    filters = _band_filter_kwargs(low, high)
    base_qs = Manor.objects.filter(
        bot_profile__isnull=True,
        user__is_staff=False,
        user__is_superuser=False,
        last_active_at__gte=now - timedelta(days=active_sample_days),
        **filters,
    )
    regional_qs = base_qs.filter(region=region) if region else Manor.objects.none()
    regional_min_sample_size = max(1, int(projection_config.get("regional_min_sample_size") or 5))
    qs = regional_qs if regional_qs.count() >= regional_min_sample_size else base_qs
    count = qs.count()
    if count <= 0:
        return None

    sample_size = max(1, int(projection_config.get("real_projection_sample_size") or 25))
    sample_size = min(sample_size, count)
    stable_seed = int(sample_seed if sample_seed is not None else rng.getrandbits(63))
    candidate_ids = list(qs.values_list("id", flat=True))
    candidate_ids.sort(
        key=lambda manor_id: blake2b(
            f"{stable_seed}:{int(manor_id)}".encode("ascii"),
            digest_size=8,
        ).digest()
    )
    selected_ids = candidate_ids[:sample_size]
    samples = list(
        qs.filter(id__in=selected_ids)
        .order_by("id")
        .annotate(
            sampled_building_level=Max(
                "buildings__level",
                filter=Q(buildings__building_type__key__in=CORE_BUILDING_KEYS),
            ),
            sampled_guest_count=Count("guests", distinct=True),
            sampled_guest_level=Max("guests__level"),
        )
        .values("id", "prestige", "sampled_building_level", "sampled_guest_count", "sampled_guest_level")
    )
    troop_totals = {
        row["manor_id"]: int(row["total"] or 0)
        for row in PlayerTroop.objects.filter(manor_id__in=selected_ids).values("manor_id").annotate(total=Sum("count"))
    }
    quantile_by_key = {"p25": 0.25, "p50": 0.50, "p75": 0.75}
    quantile = quantile_by_key.get(str(strength_quantile), 0.50)
    building_level = max(
        1,
        nearest_rank_quantile([int(row["sampled_building_level"] or 1) for row in samples], quantile),
    )
    guest_count = max(
        1,
        min(8, nearest_rank_quantile([int(row["sampled_guest_count"] or 1) for row in samples], quantile)),
    )
    guest_level = max(
        1,
        nearest_rank_quantile(
            [int(row["sampled_guest_level"] or building_level) for row in samples],
            quantile,
        ),
    )
    troop_count = max(
        0,
        nearest_rank_quantile([troop_totals.get(int(row["id"]), 0) for row in samples], quantile),
    )
    prestige = nearest_rank_quantile([int(row["prestige"] or low) for row in samples], quantile)
    if prestige > 0:
        jitter_bps = max(0, int(projection_config.get("real_projection_jitter_bps") or 0))
        jitter = int(prestige * jitter_bps / 10_000)
        prestige += rng.randint(-jitter, jitter)
    upper = high if high is not None else max(low + 1, prestige + 1)
    prestige = max(low, min(max(low, upper - 1), prestige))

    return BotProjectionConfig(
        prestige=prestige,
        building_level=max(1, int(building_level)),
        guest_count=guest_count,
        guest_level=guest_level,
        troop_count=troop_count,
    )


def _projection_for_band(
    band: str,
    low: int,
    high: int | None,
    rng: random.Random,
    *,
    region: str | None = None,
    config: dict[str, Any] | None = None,
    sample_seed: int | None = None,
    archetype: str | None = None,
) -> BotProjectionConfig:
    config = config or load_virtual_player_config()
    quantile_weights = (config.get("projection") or {}).get("strength_quantile_weights") or {
        "p25": 25,
        "p50": 50,
        "p75": 25,
    }
    strength_quantile = (
        "p25"
        if archetype == BotProfile.Archetype.ABANDONED
        else choose_strength_quantile(int(sample_seed), quantile_weights) if sample_seed is not None else "p50"
    )
    sampled = _projection_from_real_players(
        region=region,
        low=low,
        high=high,
        rng=rng,
        config=config,
        sample_seed=sample_seed,
        strength_quantile=strength_quantile,
    )
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


def _apply_persona_to_projection(
    projection: BotProjectionConfig,
    *,
    archetype: str,
    config: dict[str, Any],
    growth_seed: int,
) -> BotProjectionConfig:
    targets = apply_combat_persona(
        {
            "guest_level": projection.guest_level,
            "guest_count": projection.guest_count,
            "troop_count": projection.troop_count,
        },
        str(archetype),
        config=config.get("combat_personas") or {},
    )
    return BotProjectionConfig(
        prestige=projection.prestige,
        building_level=projection.building_level,
        guest_count=targets["guest_count"],
        guest_level=targets["guest_level"],
        troop_count=apply_stable_troop_variation(targets["troop_count"], growth_seed),
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
    config = load_virtual_player_config()
    for _idx in range(count):
        seed = rng.randint(1, 2_147_483_647)
        selected_archetype = archetype or _weighted_archetype(rng)
        profiles.append(
            create_virtual_player(
                region=region,
                prestige_band=prestige_band,
                archetype=selected_archetype,
                growth_seed=seed,
                now=now,
                projection=_projection_for_band(
                    prestige_band,
                    low,
                    high,
                    rng,
                    region=region,
                    config=config,
                    sample_seed=seed,
                    archetype=selected_archetype,
                ),
            )
        )
    return profiles


def _project_technologies(manor: Manor, *, level: int, config: dict[str, Any]) -> None:
    keys = _configured_technology_keys(config)
    if not keys:
        return
    target_level = max(0, int(level))
    PlayerTechnology.objects.bulk_create(
        [PlayerTechnology(manor=manor, tech_key=key, level=target_level, is_upgrading=False) for key in keys],
        ignore_conflicts=True,
    )
    technologies = list(PlayerTechnology.objects.filter(manor=manor, tech_key__in=keys))
    for technology in technologies:
        technology.level = target_level
        technology.is_upgrading = False
    PlayerTechnology.objects.bulk_update(technologies, ["level", "is_upgrading"])


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


def _grant_skills_to_target(
    guest: Guest,
    *,
    rng: random.Random,
    skill_keys: list[str],
    target_total: int,
    preferred_high_tier_keys: set[str],
    prefer_passive_focus: bool,
) -> None:
    existing_records = list(guest.guest_skills.select_related("skill"))
    existing_ids = {record.skill_id for record in existing_records}
    remaining_slots = max(0, int(GUEST.MAX_SKILL_SLOTS) - len(existing_ids))
    needed = min(remaining_slots, max(0, int(target_total) - len(existing_ids)))
    if needed <= 0:
        return

    candidates = list(Skill.objects.filter(key__in=skill_keys).exclude(id__in=existing_ids))
    candidates = [skill for skill in candidates if _guest_meets_skill_requirements(guest, skill)]
    rng.shuffle(candidates)
    candidates.sort(key=lambda skill: 0 if skill.key in preferred_high_tier_keys else 1)

    selected: list[Skill] = []
    if prefer_passive_focus and int(target_total) >= 2:
        desired_kinds = [SkillKind.ACTIVE, *([SkillKind.PASSIVE] * (min(int(target_total), 3) - 1))]
        existing_kinds = [record.skill.kind for record in existing_records]
        for kind in desired_kinds:
            if existing_kinds.count(kind) + sum(skill.kind == kind for skill in selected) >= desired_kinds.count(kind):
                continue
            candidate = next((skill for skill in candidates if skill.kind == kind and skill not in selected), None)
            if candidate is not None:
                selected.append(candidate)
                if len(selected) >= needed:
                    break
    for candidate in candidates:
        if len(selected) >= needed:
            break
        if candidate not in selected:
            selected.append(candidate)
    if selected:
        GuestSkill.objects.bulk_create(
            [GuestSkill(guest=guest, skill=skill, source=GuestSkill.Source.BOOK) for skill in selected],
            ignore_conflicts=True,
        )


def _grant_configured_extra_skills(
    guest: Guest,
    *,
    growth_stage: int,
    rng: random.Random,
    config: dict[str, Any],
) -> None:
    projection = config.get("projection") or {}
    early_stage_max = max(0, int(projection.get("early_stage_skill_max") or 6))
    if int(growth_stage) <= early_stage_max:
        target_total = _range_value(rng, projection.get("early_stage_skill_count"), default=(0, 1))
        _grant_skills_to_target(
            guest,
            rng=rng,
            skill_keys=_configured_keys(config, "extra_skill_keys"),
            target_total=target_total,
            preferred_high_tier_keys=set(),
            prefer_passive_focus=False,
        )
        return

    high_tier_keys = _configured_keys(config, "high_tier_skill_keys")
    high_tier_chance = _chance_value(projection.get("high_tier_skill_chance"), default=0.0)
    granted_high_tier_count = 0
    if high_tier_chance > 0 and rng.random() < high_tier_chance:
        granted_high_tier_count = _range_value(rng, projection.get("high_tier_skills_per_guest"), default=(1, 1))
    target_total = min(
        int(GUEST.MAX_SKILL_SLOTS),
        guest.guest_skills.count()
        + granted_high_tier_count
        + _range_value(rng, projection.get("extra_skills_per_guest"), default=(0, 0)),
    )
    _grant_skills_to_target(
        guest,
        rng=rng,
        skill_keys=[*high_tier_keys, *_configured_keys(config, "extra_skill_keys")],
        target_total=target_total,
        preferred_high_tier_keys=set(high_tier_keys) if granted_high_tier_count else set(),
        prefer_passive_focus=rng.random()
        < _chance_value(projection.get("multi_skill_passive_focus_chance"), default=0.75),
    )


def _equip_template(guest: Guest, template: GearTemplate) -> None:
    gear = GearItem.objects.create(manor=guest.manor, template=template, guest=guest)
    updates = {"attack_bonus", "defense_bonus"}
    apply_template_stats_to_guest(guest, gear.template, +1, updates)
    guest.save(update_fields=list(updates))


def _gear_rarity_rank(template: GearTemplate) -> int:
    return int(GEAR_RARITY_RANK.get(str(template.rarity), -1))


def _gear_template_power(template: GearTemplate) -> int:
    extra_stats = template.extra_stats if isinstance(template.extra_stats, dict) else {}
    return (
        int(template.attack_bonus or 0)
        + int(template.defense_bonus or 0)
        + sum(int(value or 0) for value in extra_stats.values() if isinstance(value, int))
    )


def _gear_max_rarity_for_stage(growth_stage: int, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("gear_max_rarity_by_stage") or {}
    if not isinstance(configured, dict):
        configured = {}
    selected_rank = -1
    selected_stage = -1
    for raw_stage, rarity in configured.items():
        try:
            stage = int(raw_stage)
        except (TypeError, ValueError):
            continue
        rank = GEAR_RARITY_RANK.get(str(rarity), -1)
        if stage <= int(growth_stage) and stage >= selected_stage and rank >= 0:
            selected_stage = stage
            selected_rank = rank
    if selected_rank >= 0:
        return selected_rank
    return GEAR_RARITY_RANK[GuestRarity.GREEN]


def _remove_virtual_gear(guest: Guest, gear: GearItem, *, updates: set[str]) -> None:
    apply_template_stats_to_guest(guest, gear.template, -1, updates)
    gear.delete()


def _reconcile_guest_gear(
    guest: Guest,
    *,
    growth_stage: int,
    rng: random.Random,
    config: dict[str, Any],
) -> None:
    templates = [
        template
        for template in _configured_gear_templates(config)
        if _gear_rarity_rank(template) <= _gear_max_rarity_for_stage(growth_stage, config)
    ]
    if not templates:
        return

    templates_by_slot: dict[str, list[GearTemplate]] = {}
    for template in templates:
        templates_by_slot.setdefault(str(template.slot), []).append(template)
    for candidates in templates_by_slot.values():
        rng.shuffle(candidates)
        candidates.sort(
            key=lambda template: (_gear_rarity_rank(template), _gear_template_power(template)), reverse=True
        )

    existing_by_slot: dict[str, list[GearItem]] = {}
    for gear in guest.gear_items.select_related("template"):
        existing_by_slot.setdefault(str(gear.template.slot), []).append(gear)

    updates = {"attack_bonus", "defense_bonus"}
    for slot in GearSlot:
        slot_key = slot.value
        capacity = slot_capacity(slot_key)
        candidates = templates_by_slot.get(slot_key, [])
        if not candidates:
            continue
        desired = candidates[:capacity]
        current = existing_by_slot.get(slot_key, [])
        current.sort(
            key=lambda gear: (_gear_rarity_rank(gear.template), _gear_template_power(gear.template)), reverse=True
        )

        kept: list[GearItem] = []
        seen_templates: set[int] = set()
        for gear in current:
            if gear.template_id in seen_templates or len(kept) >= capacity:
                _remove_virtual_gear(guest, gear, updates=updates)
                continue
            seen_templates.add(gear.template_id)
            kept.append(gear)

        for candidate in desired:
            if any(gear.template_id == candidate.id for gear in kept):
                continue
            weaker = [gear for gear in kept if _gear_rarity_rank(gear.template) < _gear_rarity_rank(candidate)]
            if weaker:
                replaced = min(
                    weaker, key=lambda gear: (_gear_rarity_rank(gear.template), _gear_template_power(gear.template))
                )
                _remove_virtual_gear(guest, replaced, updates=updates)
                kept.remove(replaced)
            elif len(kept) >= capacity:
                continue
            _equip_template(guest, candidate)
            kept.append(guest.gear_items.select_related("template").get(template=candidate))

    guest.save(update_fields=list(updates))
    apply_set_bonuses(guest)
    if guest.current_hp > guest.max_hp:
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])


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


def _diverse_guest_templates(templates: list[GuestTemplate], *, rng: random.Random) -> list[GuestTemplate]:
    if len(templates) <= 1:
        return templates
    usage_counts = {
        row["template_id"]: row["count"]
        for row in (
            Guest.objects.filter(
                manor__bot_profile__state__in=[
                    BotProfile.State.ACTIVE,
                    BotProfile.State.SLOWING,
                    BotProfile.State.ABANDONED,
                ],
                template__in=templates,
            )
            .values("template_id")
            .annotate(count=Count("id"))
        )
    }
    diversified = list(templates)
    rng.shuffle(diversified)
    diversified.sort(key=lambda template: int(usage_counts.get(template.id, 0)))
    return diversified


def _project_guests_and_gear(
    manor: Manor,
    *,
    count: int,
    level: int,
    rng: random.Random,
    config: dict[str, Any],
    archetype: str,
    growth_stage: int,
    grant_configured_skills: bool = True,
) -> None:
    guest_keys = _configured_model_keys(config, "guest_template_keys", GuestTemplate)
    if not guest_keys or count <= 0:
        return
    templates = list(
        GuestTemplate.objects.filter(key__in=guest_keys).order_by("key").prefetch_related("initial_skills")
    )
    if not templates:
        return
    templates = _diverse_guest_templates(templates, rng=rng)
    for idx in range(max(0, int(count))):
        template = templates[idx % len(templates)]
        guest = create_guest_from_template(manor=manor, template=template, rng=rng, grant_skills=True)
        guest.level = max(1, int(level))
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["level", "current_hp"])
        _grant_extra_template_skills(guest)
        if grant_configured_skills:
            _grant_configured_extra_skills(guest, growth_stage=growth_stage, rng=rng, config=config)
        _reconcile_guest_gear(guest, growth_stage=growth_stage, rng=rng, config=config)


def _project_troops(manor: Manor, *, count: int, config: dict[str, Any]) -> None:
    troop_keys = _configured_model_keys(config, "troop_template_keys", TroopTemplate)
    if not troop_keys:
        return
    templates = list(TroopTemplate.objects.filter(key__in=troop_keys).order_by("key"))
    if not templates:
        return
    PlayerTroop.objects.filter(manor=manor).exclude(troop_template__in=templates).update(count=0)
    per_type, remainder = divmod(max(0, int(count)), len(templates))
    target_counts = {
        template.id: per_type + (1 if index < remainder else 0) for index, template in enumerate(templates)
    }
    PlayerTroop.objects.bulk_create(
        [PlayerTroop(manor=manor, troop_template=template, count=target_counts[template.id]) for template in templates],
        ignore_conflicts=True,
    )
    troops = list(PlayerTroop.objects.filter(manor=manor, troop_template_id__in=target_counts))
    for troop in troops:
        troop.count = target_counts[troop.troop_template_id]
    PlayerTroop.objects.bulk_update(troops, ["count"])


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


def _inventory_template_slot_count(archetype: str, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("inventory_template_slots_by_archetype") or {}
    if isinstance(configured, dict) and archetype in configured:
        return max(1, int(configured[archetype] or 1))
    default_slots = DEFAULT_VIRTUAL_PLAYER_CONFIG["projection"]["inventory_template_slots_by_archetype"]
    return max(1, int(default_slots.get(archetype, default_slots[BotProfile.Archetype.BALANCED.value])))


def _inventory_effect_weight(template: ItemTemplate, *, archetype: str, config: dict[str, Any]) -> int:
    projection = config.get("projection") or {}
    configured = projection.get("inventory_effect_type_weights") or {}
    archetype_weights = configured.get(archetype) if isinstance(configured, dict) else None
    if not isinstance(archetype_weights, dict):
        archetype_weights = {}
    return max(1, int(archetype_weights.get(str(template.effect_type), 1) or 1))


def _select_inventory_template_pool(
    profile: BotProfile,
    templates: list[ItemTemplate],
    *,
    archetype: str,
    rng: random.Random,
    config: dict[str, Any],
) -> list[ItemTemplate]:
    """Keep a small archetype-shaped inventory pool, spreading templates across live bots."""
    slot_count = min(_inventory_template_slot_count(archetype, config), len(templates))
    if slot_count <= 0:
        return []

    by_key = {template.key: template for template in templates}
    selected = [by_key[key] for key in profile.inventory_template_keys if key in by_key]
    selected = list(dict.fromkeys(selected))[:slot_count]
    if len(selected) >= slot_count:
        return selected

    usage_counts = {
        row["template_id"]: row["manor_count"]
        for row in (
            InventoryItem.objects.filter(
                manor__bot_profile__state__in=[
                    BotProfile.State.ACTIVE,
                    BotProfile.State.SLOWING,
                    BotProfile.State.ABANDONED,
                ],
                template__in=templates,
            )
            .values("template_id")
            .annotate(manor_count=Count("manor_id", distinct=True))
        )
    }
    candidates = [template for template in templates if template not in selected]
    while candidates and len(selected) < slot_count:
        weighted_candidates = [
            (
                template,
                _inventory_effect_weight(template, archetype=archetype, config=config)
                / (1 + int(usage_counts.get(template.id, 0))),
            )
            for template in candidates
        ]
        total_weight = sum(weight for _template, weight in weighted_candidates)
        target = rng.uniform(0, total_weight)
        cumulative = 0.0
        chosen = weighted_candidates[-1][0]
        for template, weight in weighted_candidates:
            cumulative += weight
            if target <= cumulative:
                chosen = template
                break
        selected.append(chosen)
        candidates.remove(chosen)
        usage_counts[chosen.id] = int(usage_counts.get(chosen.id, 0)) + 1
    return selected


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
    profile: BotProfile,
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
    candidate_templates = list(ItemTemplate.objects.filter(key__in=unique_keys, tradeable=True).order_by("key"))
    if not candidate_templates:
        return
    templates = _select_inventory_template_pool(
        profile,
        candidate_templates,
        archetype=str(archetype),
        rng=rng,
        config=config,
    )
    pool_keys = [template.key for template in templates]
    if profile.inventory_template_keys != pool_keys:
        profile.inventory_template_keys = pool_keys
        profile.save(update_fields=["inventory_template_keys", "updated_at"])

    now = now or timezone.now()
    stale_template_ids = [
        template.id
        for template in candidate_templates
        if template.key not in pool_keys and template.effect_type in VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES
    ]
    if stale_template_ids:
        stale_item_ids = list(
            InventoryItem.objects.select_for_update()
            .filter(
                manor=manor,
                template_id__in=stale_template_ids,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .values_list("id", flat=True)
        )
        if stale_item_ids:
            InventoryItem.objects.filter(id__in=stale_item_ids).delete()

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
    lifecycle_personas = config.get("lifecycle_personas") or DEFAULT_VIRTUAL_PLAYER_CONFIG["lifecycle_personas"]
    dates = choose_lifecycle(rng, now, lifecycle_personas)
    lifecycle = config.get("lifecycle") or {}
    next_growth_hours = _range_value(rng, lifecycle.get("next_growth_hours"), default=(2, 18))
    next_growth_at = now + timedelta(hours=next_growth_hours, minutes=rng.randint(0, 59))
    return next_growth_at, dates.abandon_at, dates.retire_at


def _growth_stage_cap_for_band(prestige_band: str, config: dict[str, Any]) -> int:
    growth = config.get("growth") or {}
    stage_caps = growth.get("stage_caps") or {}
    default_caps = DEFAULT_VIRTUAL_PLAYER_CONFIG["growth"]["stage_caps"]
    raw_cap = stage_caps.get(prestige_band, default_caps.get(prestige_band, max(default_caps.values())))
    return max(1, int(raw_cap or 1))


@transaction.atomic
def create_virtual_player(
    *,
    region: str,
    prestige_band: str,
    archetype: str = BotProfile.Archetype.BALANCED,
    growth_seed: int | None = None,
    now=None,
    projection: BotProjectionConfig | None = None,
    start_from_zero: bool = False,
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
    projection = _apply_persona_to_projection(
        projection,
        archetype=str(archetype),
        config=config,
        growth_seed=seed,
    )
    target_band = _prestige_bands(config).get(prestige_band)
    target_low = target_band[0] if target_band is not None else 0
    stage_cap = _growth_stage_cap_for_band(prestige_band, config)
    if start_from_zero:
        starting_projection = BotProjectionConfig(
            prestige=0,
            building_level=INITIAL_BOT_BUILDING_LEVEL,
            guest_count=min(max(0, int(projection.guest_count)), INITIAL_BOT_GUEST_COUNT),
            guest_level=INITIAL_BOT_GUEST_LEVEL,
            troop_count=max(0, min(int(projection.troop_count), 50)),
        )
    elif target_low > 0:
        target_high = target_band[1] if target_band is not None else None
        projected_prestige = max(target_low, int(projection.prestige))
        if target_high is not None:
            projected_prestige = min(projected_prestige, target_high - 1)
        starting_projection = BotProjectionConfig(
            prestige=projected_prestige,
            building_level=min(stage_cap, max(1, int(projection.building_level))),
            guest_count=max(0, int(projection.guest_count)),
            guest_level=max(1, int(projection.guest_level)),
            troop_count=max(0, int(projection.troop_count)),
        )
    else:
        starting_projection = BotProjectionConfig(
            prestige=min(max(0, int(projection.prestige)), INITIAL_BOT_PRESTIGE),
            building_level=INITIAL_BOT_BUILDING_LEVEL,
            guest_count=min(max(0, int(projection.guest_count)), INITIAL_BOT_GUEST_COUNT),
            guest_level=INITIAL_BOT_GUEST_LEVEL,
            troop_count=max(0, min(int(projection.troop_count), 50)),
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
        growth_stage=int(starting_projection.building_level),
    )
    _project_troops(manor, count=max(0, int(starting_projection.troop_count)), config=config)

    lifecycle_rng = random.Random(f"lifecycle:{seed}")
    next_growth_at, abandon_at, retire_at = _lifecycle_dates(now, lifecycle_rng, config)
    profile = BotProfile.objects.create(
        manor=manor,
        archetype=archetype,
        state=BotProfile.State.ACTIVE,
        prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        current_prestige_band=_prestige_band_for_value(int(manor.prestige or 0), config) or prestige_band,
        growth_seed=seed,
        growth_stage=min(stage_cap, max(1, int(starting_projection.building_level))),
        next_growth_at=next_growth_at,
        abandon_at=abandon_at,
        retire_at=retire_at,
        loot_budget_daily=int((config.get("projection") or {}).get("loot_budget_daily", 2_000_000) or 0),
        maintenance_started_at=now,
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


@transaction.atomic
def create_virtual_player_with_capacity(
    *,
    region: str | None,
    prestige_band: str,
    archetype: str | None = None,
    growth_seed: int | None = None,
    now=None,
    projection: BotProjectionConfig | None = None,
    start_from_zero: bool = False,
) -> PopulationMutationResult:
    current_time = now or timezone.now()
    hard_cap, maintained_count = _lock_population_capacity(now=current_time)
    if not _population_has_room(hard_cap, maintained_count):
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )

    selected_region = region or _select_virtual_player_creation_region(now=current_time)
    if selected_region is None:
        return PopulationMutationResult(
            status=PopulationMutationStatus.UNAVAILABLE,
            profile=None,
            hard_cap=hard_cap,
            maintained_count=maintained_count,
        )

    seed = int(growth_seed or random.randint(1, 2_147_483_647))
    selected_archetype = archetype or _weighted_archetype(random.Random(seed))
    profile = create_virtual_player(
        region=selected_region,
        prestige_band=prestige_band,
        archetype=selected_archetype,
        growth_seed=seed,
        now=current_time,
        projection=projection,
        start_from_zero=start_from_zero,
    )
    return PopulationMutationResult(
        status=PopulationMutationStatus.CREATED,
        profile=profile,
        hard_cap=hard_cap,
        maintained_count=maintained_count,
    )


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
    quantile_weights = (config.get("projection") or {}).get("strength_quantile_weights") or {
        "p25": 25,
        "p50": 50,
        "p75": 25,
    }
    strength_quantile = (
        "p25"
        if profile.archetype == BotProfile.Archetype.ABANDONED
        else choose_strength_quantile(profile.growth_seed, quantile_weights)
    )
    return _projection_from_real_players(
        region=profile.manor.region,
        low=low,
        high=high,
        rng=rng,
        config=config,
        sample_seed=profile.growth_seed,
        strength_quantile=strength_quantile,
    )


def _sync_profile_prestige_band(profile: BotProfile, *, config: dict[str, Any]) -> None:
    current_band = _prestige_band_for_value(int(profile.manor.prestige or 0), config)
    if not current_band or current_band == profile.current_prestige_band:
        return

    profile.current_prestige_band = current_band
    profile.save(update_fields=["current_prestige_band", "updated_at"])


def _pay_maintained_bot_salaries(profile: BotProfile, *, now) -> None:
    manor = profile.manor
    try:
        pay_all_salaries(manor, for_date=timezone.localdate(now))
    except (NoGuestsError, SalaryAlreadyPaidError):
        pass
    except InsufficientResourceError:
        logger.info(
            "Virtual player could not cover guest salaries: manor_id=%s state=%s",
            manor.id,
            profile.state,
            extra={
                "event": "virtual_player_salary_unpaid",
                "manor_id": manor.id,
                "state": profile.state,
            },
        )


def _maintain_active_profile(profile: BotProfile, *, now, config: dict[str, Any]) -> None:
    rng = random.Random(profile.growth_seed + profile.growth_stage)
    manor = profile.manor
    before_building_level = max(1, int(profile.growth_stage))
    before_guest_level = max([int(level) for level in manor.guests.values_list("level", flat=True)] or [0])
    before_troop_count = int(manor.troops.aggregate(total=Sum("count"))["total"] or 0)
    before_prestige = int(manor.prestige or 0)
    stage_cap = _growth_stage_cap_for_band(_profile_target_prestige_band(profile), config)
    projection = _maintenance_projection_from_real_players(profile, rng=rng, config=config)
    if projection is not None:
        projection = _apply_persona_to_projection(
            projection,
            archetype=str(profile.archetype),
            config=config,
            growth_seed=int(profile.growth_seed),
        )
    growth = config.get("growth") or {}
    catch_up_ratio = max(0.0, min(1.0, float(growth.get("catch_up_ratio") or 0.25)))
    if profile.state == BotProfile.State.SLOWING:
        catch_up_ratio *= max(0.0, min(1.0, float(growth.get("slowing_ratio_multiplier") or 0.5)))
    current_building_level = max(1, int(profile.growth_stage))
    projected_building_level = int(projection.building_level) if projection is not None else current_building_level + 1
    target_building_level = min(
        stage_cap,
        bounded_approach(
            current_building_level,
            max(current_building_level, projected_building_level),
            ratio=catch_up_ratio,
            min_step=1,
            max_step=max(1, int(growth.get("max_building_step") or 2)),
        ),
    )
    target_guest_count = manor.guests.count()
    current_guest_level = max([int(level) for level in manor.guests.values_list("level", flat=True)] or [1])
    target_guest_level = current_guest_level
    if projection is not None:
        target_guest_count = min(max(target_guest_count + 1, 1), max(target_guest_count, int(projection.guest_count)))
        target_guest_level = bounded_approach(
            current_guest_level,
            max(current_guest_level, int(projection.guest_level)),
            ratio=catch_up_ratio,
            min_step=1,
            max_step=max(1, int(growth.get("max_guest_level_step") or 3)),
        )

    _project_buildings(manor, level=target_building_level)
    _project_resources(manor, archetype=profile.archetype, rng=rng, config=config)
    current_prestige = int(manor.prestige or 0)
    projected_prestige = int(projection.prestige) if projection is not None else target_building_level * 250
    manor.prestige = bounded_approach(
        current_prestige,
        max(current_prestige, projected_prestige),
        ratio=catch_up_ratio,
        min_step=1,
        max_step=max(1, int(growth.get("max_prestige_step") or 500)),
    )
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
            growth_stage=target_building_level,
        )
    for guest in manor.guests.all():
        if guest.level < target_guest_level:
            guest.level = target_guest_level
            guest.current_hp = guest.max_hp
            guest.save(update_fields=["level", "current_hp"])
        _grant_configured_extra_skills(guest, growth_stage=target_building_level, rng=rng, config=config)
        _reconcile_guest_gear(guest, growth_stage=target_building_level, rng=rng, config=config)
    _pay_maintained_bot_salaries(profile, now=now)
    current_troop_count = int(manor.troops.aggregate(total=Sum("count"))["total"] or 0)
    projected_troop_count = (
        int(projection.troop_count)
        if projection is not None
        else apply_stable_troop_variation(target_building_level * 80, int(profile.growth_seed))
    )
    target_troop_count = bounded_approach(
        current_troop_count,
        max(0, projected_troop_count),
        ratio=catch_up_ratio,
        min_step=1,
        max_step=max(50, target_building_level * 80),
    )
    _project_troops(manor, count=max(0, target_troop_count), config=config)
    _replenish_inventory_stock(
        profile,
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
    logger.info(
        "Virtual player maintained: manor_id=%s region=%s archetype=%s building=%s->%s prestige=%s->%s",
        manor.id,
        manor.region,
        profile.archetype,
        before_building_level,
        target_building_level,
        before_prestige,
        manor.prestige,
        extra={
            "event": "virtual_player_maintained",
            "manor_id": manor.id,
            "region": manor.region,
            "archetype": profile.archetype,
            "state": profile.state,
            "target_prestige_band": _profile_target_prestige_band(profile),
            "current_prestige_band": profile.current_prestige_band,
            "before_building_level": before_building_level,
            "after_building_level": target_building_level,
            "before_guest_level": before_guest_level,
            "after_guest_level": target_guest_level,
            "before_troop_count": before_troop_count,
            "after_troop_count": target_troop_count,
            "before_prestige": before_prestige,
            "after_prestige": int(manor.prestige or 0),
        },
    )


@transaction.atomic
def accelerate_virtual_player_growth(
    profile_id: int,
    *,
    now=None,
) -> AcceleratedGrowthOutcome:
    current_time = now or timezone.now()
    profile = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            pk=profile_id,
            state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING],
        )
        .first()
    )
    if profile is None:
        state = BotProfile.objects.filter(pk=profile_id).values_list("state", flat=True).first()
        if state in [BotProfile.State.ACTIVE, BotProfile.State.SLOWING]:
            return AcceleratedGrowthOutcome.BUSY
        return AcceleratedGrowthOutcome.INELIGIBLE

    original_next_growth_at = profile.next_growth_at
    _maintain_active_profile(profile, now=current_time, config=load_virtual_player_config())
    profile.refresh_from_db(fields=["next_growth_at"])
    if original_next_growth_at != profile.next_growth_at:
        profile.next_growth_at = original_next_growth_at
        profile.save(update_fields=["next_growth_at", "updated_at"])
    return AcceleratedGrowthOutcome.GROWN


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


def _maintenance_cycle_started_at(profile: BotProfile):
    return profile.maintenance_started_at or profile.created_at


def _has_repeated_empty_raids(profile: BotProfile, *, now, config: dict[str, Any]) -> bool:
    lifecycle = config.get("lifecycle") or {}
    threshold = int(lifecycle.get("empty_hit_stale_threshold") or 0)
    if threshold <= 0 or not _is_resource_empty(profile.manor):
        return False
    window_hours = int(lifecycle.get("empty_hit_window_hours") or 24)
    since = now - timedelta(hours=max(1, window_hours))
    since = max(since, _maintenance_cycle_started_at(profile))
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
    maintenance_started_at = _maintenance_cycle_started_at(profile)
    if maintenance_started_at > cutoff:
        return False
    since = max(cutoff, maintenance_started_at)
    return not (
        RaidRun.objects.filter(defender=profile.manor, started_at__gte=since).exists()
        or ScoutRecord.objects.filter(defender=profile.manor, started_at__gte=since).exists()
    )


def _mark_profile_retired(profile: BotProfile, *, now) -> bool:
    if (
        ArenaVirtualReserveMember.objects.filter(profile_id=profile.id).exists()
        or profile.manor_id in _arena_protected_bot_manor_ids()
    ):
        profile.next_growth_at = now + timedelta(hours=1)
        profile.save(update_fields=["next_growth_at", "updated_at"])
        return False
    profile.state = BotProfile.State.RETIRED
    profile.next_growth_at = now
    profile.maintenance_stopped_at = now
    profile.save(update_fields=["state", "next_growth_at", "maintenance_stopped_at", "updated_at"])
    return True


@transaction.atomic
def retire_virtual_player_if_unprotected(profile_id: int, *, now=None) -> bool:
    current_time = now or timezone.now()
    profile = (
        BotProfile.objects.select_for_update()
        .filter(pk=profile_id)
        .exclude(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED])
        .first()
    )
    if profile is None:
        return False
    return _mark_profile_retired(profile, now=current_time)


def _maintain_profile(profile: BotProfile, *, now, config: dict[str, Any]) -> None:
    _sync_profile_prestige_band(profile, config=config)

    if _has_repeated_empty_raids(profile, now=now, config=config) or _has_long_no_interaction(
        profile, now=now, config=config
    ):
        _mark_profile_retired(profile, now=now)
        return

    if profile.retire_at <= now:
        _mark_profile_retired(profile, now=now)
        return

    if profile.state == BotProfile.State.ABANDONED:
        profile.next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile.save(update_fields=["next_growth_at", "updated_at"])
        return

    if profile.abandon_at <= now:
        profile.state = BotProfile.State.ABANDONED
        profile.next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile.save(update_fields=["state", "next_growth_at", "updated_at"])
        return

    maintenance_started_at = _maintenance_cycle_started_at(profile)
    active_duration = max(timedelta(days=1), profile.abandon_at - maintenance_started_at)
    slowing_at = profile.abandon_at - max(timedelta(days=1), active_duration * 0.2)
    if profile.state == BotProfile.State.ACTIVE and slowing_at <= now:
        profile.state = BotProfile.State.SLOWING
        profile.next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile.last_planned_at = now
        profile.save(update_fields=["state", "next_growth_at", "last_planned_at", "updated_at"])
        _pay_maintained_bot_salaries(profile, now=now)
        return

    if profile.archetype == BotProfile.Archetype.ABANDONED:
        profile.next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile.save(update_fields=["next_growth_at", "updated_at"])
        return

    _maintain_active_profile(profile, now=now, config=config)


def maintain_due_virtual_players(*, now=None, limit: int = 100) -> int:
    now = now or timezone.now()
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return 0
    profile_ids = list(
        BotProfile.objects.exclude(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED])
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
                .exclude(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED])
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
    eligible_states = [
        BotProfile.State.ACTIVE,
        BotProfile.State.SLOWING,
        BotProfile.State.ABANDONED,
    ]
    with transaction.atomic():
        protected_manor_ids = _arena_protected_bot_manor_ids()
        stale_ids = list(
            _maintained_bot_queryset()
            .select_for_update(skip_locked=True)
            .filter(
                state__in=eligible_states,
                arena_virtual_reserve__isnull=True,
            )
            .exclude(manor_id__in=protected_manor_ids)
            .order_by("last_planned_at", "created_at", "id")
            .values_list("id", flat=True)[:excess]
        )
        if not stale_ids:
            return 0
        if ownership_guard is not None:
            ownership_guard()
        retired_count = (
            BotProfile.objects.filter(
                id__in=stale_ids,
                state__in=eligible_states,
                arena_virtual_reserve__isnull=True,
            )
            .exclude(manor_id__in=_arena_protected_bot_manor_ids())
            .update(
                state=BotProfile.State.RETIRED,
                next_growth_at=now,
                maintenance_stopped_at=now,
            )
        )
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


def _retire_excess_population_cells(
    population_plan: PopulationPlan,
    *,
    config: dict[str, Any],
    now,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    retired_count = 0
    total_excess = 0
    bands = _prestige_bands(config)
    target_based = _uses_regional_population_planning()
    for cell in population_plan.cells:
        excess = int(cell.excess)
        if excess <= 0 or cell.prestige_band not in bands:
            continue
        membership_filter = _population_cell_membership_filter(
            cell.prestige_band,
            config=config,
            target_based=target_based,
        )
        eligible_states = [
            BotProfile.State.ACTIVE,
            BotProfile.State.SLOWING,
            BotProfile.State.ABANDONED,
        ]
        with transaction.atomic():
            protected_manor_ids = _arena_protected_bot_manor_ids()
            stale_ids = list(
                _maintained_bot_queryset()
                .select_for_update(skip_locked=True)
                .filter(membership_filter)
                .filter(
                    manor__region=cell.region,
                    state__in=eligible_states,
                    arena_virtual_reserve__isnull=True,
                )
                .exclude(manor_id__in=protected_manor_ids)
                .order_by("last_planned_at", "created_at", "id")
                .values_list("id", flat=True)[:excess]
            )
            if not stale_ids:
                continue
            if ownership_guard is not None:
                ownership_guard()
            updated = (
                BotProfile.objects.filter(
                    id__in=stale_ids,
                    manor__region=cell.region,
                    state__in=eligible_states,
                    arena_virtual_reserve__isnull=True,
                )
                .filter(membership_filter)
                .exclude(manor_id__in=_arena_protected_bot_manor_ids())
                .update(
                    state=BotProfile.State.RETIRED,
                    next_growth_at=now,
                    maintenance_stopped_at=now,
                )
            )
        retired_count += updated
        total_excess += excess

    if retired_count > 0:
        logger.info(
            "Virtual player overpopulation retired by cell: target=%s excess=%s retired_count=%s",
            population_plan.target_total,
            total_excess,
            retired_count,
            extra={
                "event": "virtual_player_overpopulation_retired",
                "target": population_plan.target_total,
                "excess": total_excess,
                "retired_count": retired_count,
            },
        )
    return retired_count


def plan_virtual_player_population(*, now=None) -> dict[str, Any]:
    config = load_virtual_player_config()
    now = now or timezone.now()
    active_real_players = _active_real_player_count(now)
    population_plan = _build_population_plan(config, now=now)
    maintained_bots = sum(cell.maintained_supply for cell in population_plan.cells)
    attackable_bots = sum(cell.attackable_supply for cell in population_plan.cells)
    population = config.get("population") or {}
    cell_floor = max(
        0,
        _configured_population_value(
            population,
            "cell_floor",
            legacy_field="min_attackable_per_band",
            default=4,
        ),
    )
    cell_multiplier = max(
        0,
        _configured_population_value(
            population,
            "cell_active_multiplier",
            legacy_field="active_player_multiplier",
            default=2,
        ),
    )
    payload = {
        "enabled": bool(config.get("enabled", True)),
        "regions": _regions(),
        "prestige_bands": list(_prestige_bands(config).keys()),
        "active_real_players": active_real_players,
        "target_bot_total": population_plan.target_total,
        "active_bots": maintained_bots,
        "maintained_bots": maintained_bots,
        "attackable_bots": attackable_bots,
        "hard_cap": population_plan.hard_cap,
        "region_targets": population_plan.region_targets,
        "config_summary": {
            "active_window_days": max(1, int(population.get("active_window_days") or 7)),
            "cell_floor": cell_floor,
            "cell_active_multiplier": cell_multiplier,
            "region_floor": max(0, _population_config_int(population, "region_floor", 8)),
            "region_active_multiplier": max(
                0,
                _population_config_int(population, "region_active_multiplier", 8),
            ),
            "global_floor": max(0, _population_config_int(population, "global_floor", 32)),
            "global_active_multiplier": max(
                0,
                _population_config_int(population, "global_active_multiplier", 20),
            ),
            "exploration_supply": max(0, int(population.get("exploration_supply") or 0)),
        },
        "cells": [
            {
                "region": cell.region,
                "prestige_band": cell.prestige_band,
                "active_real": cell.active_real,
                "maintained_supply": cell.maintained_supply,
                "attackable_supply": cell.attackable_supply,
                "search_demand": cell.search_demand,
                "target": cell.target,
                "deficit": cell.deficit,
                "structural_deficit": cell.structural_deficit,
                "attackable_target": cell.attackable_target,
                "attackable_deficit": cell.attackable_deficit,
                "excess": cell.excess,
            }
            for cell in population_plan.cells
        ],
        "planned_at": now.isoformat(),
    }
    logger.info(
        "Virtual player population planned: active_real=%s maintained=%s attackable=%s target=%s",
        active_real_players,
        maintained_bots,
        attackable_bots,
        population_plan.target_total,
        extra={
            "event": "virtual_player_population_planned",
            "active_real_players": active_real_players,
            "maintained_bots": maintained_bots,
            "maintained_count": maintained_bots,
            "attackable_bots": attackable_bots,
            "target_bot_total": population_plan.target_total,
            "hard_cap": population_plan.hard_cap,
            "region_targets": population_plan.region_targets,
            "reactivated_count": 0,
            "created_count": 0,
            "retired_count": 0,
            "cells": payload["cells"],
        },
    )
    return payload


def _create_backfill_demanded_players(
    *,
    demands: list[dict[str, Any]],
    bands: dict[str, tuple[int, int | None]],
    hard_cap: int,
    limit: int,
    now,
    rng: random.Random,
    config: dict[str, Any] | None = None,
    evaluated_profile_ids: set[int] | None = None,
    ownership_guard: Callable[[], None] | None = None,
) -> int:
    config = config or load_virtual_player_config()
    evaluated_profile_ids = evaluated_profile_ids if evaluated_profile_ids is not None else set()
    created = 0
    reactivated_count = 0
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
        reactivated_before_demand = reactivated_count
        cap_reached = False
        while created < limit and created - created_before_demand < needed:
            seed = rng.randint(1, 2_147_483_647)
            selected_archetype = _weighted_archetype(rng)
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
                mutation = _reactivate_or_create_virtual_player(
                    region=region,
                    prestige_band=band_name,
                    low=low,
                    high=high,
                    archetype=selected_archetype,
                    growth_seed=seed,
                    now=now,
                    config=config,
                    evaluated_profile_ids=evaluated_profile_ids,
                    ownership_guard=ownership_guard,
                    require_population_deficit=True,
                    include_target_pipeline=True,
                    projection_factory=lambda: _projection_for_band(
                        band_name,
                        low,
                        high,
                        rng,
                        region=region,
                        config=config,
                        sample_seed=seed,
                        archetype=selected_archetype,
                    ),
                )
                if mutation.status is PopulationMutationStatus.CAP_REACHED:
                    cap_reached = True
                    break
                if mutation.profile is None:
                    break
                if mutation.status is PopulationMutationStatus.REACTIVATED:
                    reactivated_count += 1
            created += 1
        if needed > 0:
            created_for_demand = created - created_before_demand
            reactivated_for_demand = reactivated_count - reactivated_before_demand
            newly_created_for_demand = created_for_demand - reactivated_for_demand
            logger.info(
                "Virtual player backfill demand provisioned: region=%s prestige_band=%s processed=%s needed=%s",
                region,
                band_name,
                created_for_demand,
                needed,
                extra={
                    "event": "virtual_player_backfill_demand_provisioned",
                    "region": region,
                    "prestige_band": band_name,
                    "processed_count": created_for_demand,
                    "created_count": newly_created_for_demand,
                    "reactivated_count": reactivated_for_demand,
                    "needed": needed,
                },
            )
        if cap_reached or created >= limit:
            return created
    return created


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
    bands = _prestige_bands(config)
    rng = random.Random(int(now.timestamp()))
    if limit is None:
        limit = _range_value(rng, population.get("rolling_batch_size"), default=(3, 12))
    limit = max(0, int(limit))
    population_plan = _build_population_plan(config, now=now)
    if _uses_regional_population_planning() and limit > 0:
        rebalance_virtual_player_target_bands(population_plan, limit=limit)
        population_plan = _build_population_plan(config, now=now)
    hard_cap = population_plan.hard_cap
    retired_for_capacity = _retire_excess_population_cells(
        population_plan,
        config=config,
        now=now,
        ownership_guard=ownership_guard,
    )
    active_bot_count = _maintained_bot_count()
    if hard_cap > 0 and active_bot_count >= hard_cap:
        return 0

    if limit <= 0:
        return 0

    if not bands:
        return retired_for_capacity

    evaluated_profile_ids: set[int] = set()
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
        config=config,
        evaluated_profile_ids=evaluated_profile_ids,
        ownership_guard=ownership_guard,
    )

    refreshed_plan = _build_population_plan(config, now=now)
    deficit_cells: list[dict[str, Any]] = [
        {
            "region": cell.region,
            "band_name": cell.prestige_band,
            "low": bands[cell.prestige_band][0],
            "high": bands[cell.prestige_band][1],
            "deficit": cell.deficit,
            "search_demand": cell.search_demand,
        }
        for cell in refreshed_plan.cells
        if cell.prestige_band in bands and cell.deficit > 0
    ]
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
            selected_archetype = _weighted_archetype(rng)
            if ownership_guard is not None:
                ownership_guard()
            mutation = _reactivate_or_create_virtual_player(
                region=str(cell["region"]),
                prestige_band=str(cell["band_name"]),
                low=int(cell["low"]),
                high=cell["high"],
                archetype=selected_archetype,
                growth_seed=seed,
                now=now,
                config=config,
                evaluated_profile_ids=evaluated_profile_ids,
                ownership_guard=ownership_guard,
                require_population_deficit=True,
                include_target_pipeline=int(cell["search_demand"]) > 0,
                projection_factory=lambda: _projection_for_band(
                    str(cell["band_name"]),
                    int(cell["low"]),
                    cell["high"],
                    rng,
                    region=str(cell["region"]),
                    config=config,
                    sample_seed=seed,
                    archetype=selected_archetype,
                ),
            )
            if mutation.status is PopulationMutationStatus.CAP_REACHED:
                return created
            if mutation.profile is None:
                continue
            cell["deficit"] = int(cell["deficit"]) - 1
            created += 1
            progressed = True
        if not progressed:
            break
        deficit_cells = [cell for cell in deficit_cells if int(cell["deficit"]) > 0]
    return created
