"""赛季规则、阵营奖励及基于明确数据来源的人物候选目录。"""

from __future__ import annotations

import copy
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet

from core.utils.yaml_loader import load_yaml_data
from gameplay.models import Manor
from guests.models import Guest, GuestRarity, GuestTemplate
from guests.services.template_identity import get_owned_non_repeatable_template_ids

logger = logging.getLogger(__name__)
FACTIONS = ("fiction", "history")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_FICTION_SOURCES = frozenset(
    {
        "jinyong.yaml",
        "gulong.yaml",
        "gulong_ilrock_01.yaml",
        "liangyshen.yaml",
        "wenruian.yaml",
        "huangyi.yaml",
        "huangyi_ilrock_01.yaml",
        "suitang.yaml",
        "shuihu.yaml",
        "sanguoyanyi.yaml",
        "kunlun.yaml",
        "fengyun.yaml",
    }
)


def _positive_int(value: Any, field: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ImproperlyConfigured(f"赛季配置 {field} 必须为 1–{maximum} 的整数")
    return value


def _item_map(value: Any, field: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, dict) or (not value and not allow_empty):
        raise ImproperlyConfigured(f"赛季配置 {field} 必须为物品数量映射")
    for key, quantity in value.items():
        if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
            raise ImproperlyConfigured(f"赛季配置 {field} 含无效物品 key")
        _positive_int(quantity, f"{field}.{key}")


def _entries(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ImproperlyConfigured(f"赛季配置 {field} 必须为非空列表")
    keys: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ImproperlyConfigured(f"赛季配置 {field} 条目必须为映射")
        key = entry.get("key")
        if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key) or key in keys:
            raise ImproperlyConfigured(f"赛季配置 {field} 的 key 无效或重复")
        keys.add(key)
        if not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ImproperlyConfigured(f"赛季配置 {field}.{key} 缺少名称")
        if not isinstance(entry.get("description"), str):
            raise ImproperlyConfigured(f"赛季配置 {field}.{key} 缺少说明")
    return value


def _frozen_roster_signature(frozen: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """把冻结名单转成可哈希签名，结构错误时立即拒绝。"""
    if not isinstance(frozen, dict) or set(frozen) != set(FACTIONS):
        raise ImproperlyConfigured("赛季阵营人物快照无效")
    fiction, history = frozen["fiction"], frozen["history"]
    if not isinstance(fiction, list) or not isinstance(history, list):
        raise ImproperlyConfigured("赛季阵营人物快照无效")
    return tuple(fiction), tuple(history)


@lru_cache(maxsize=8)
def _parse_frozen_roster_keys(fiction: tuple[Any, ...], history: tuple[Any, ...]) -> dict[str, frozenset[str]]:
    """校验并转换冻结名单；同一内容只做一次正则校验（相邻赛季通常共享快照）。"""
    for keys in (fiction, history):
        if (
            not keys
            or any(not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key) for key in keys)
            or len(set(keys)) != len(keys)
        ):
            raise ImproperlyConfigured("赛季阵营人物快照无效")
    if set(fiction) & set(history):
        raise ImproperlyConfigured("赛季阵营人物快照不能重叠")
    return {"fiction": frozenset(fiction), "history": frozenset(history)}


def _validated_frozen_roster_keys(frozen: Any) -> dict[str, frozenset[str]]:
    """返回已校验的冻结名单；按内容缓存，避免每次查询重复校验数千个 key。"""
    fiction, history = _frozen_roster_signature(frozen)
    try:
        return _parse_frozen_roster_keys(fiction, history)
    except TypeError:
        # 非字符串或不可哈希的 key 会在缓存键/正则校验阶段触发，统一转为配置错误。
        raise ImproperlyConfigured("赛季阵营人物快照无效") from None


def validate_rules(rules: Any) -> dict[str, Any]:
    """拒绝可能造成无限次数、负成本或错误奖励的配置。"""
    if not isinstance(rules, dict):
        raise ImproperlyConfigured("赛季配置必须为映射")
    _positive_int(rules.get("summon_pick_count", 3), "summon_pick_count", maximum=10)
    for field, maximum in (
        ("max_guests", 10),
        ("daily_actions", 100),
        ("weekly_pve_cap", 10000),
        ("weekly_pvp_cap", 10000),
        ("pvp_daily_same_opponent", 10),
    ):
        _positive_int(rules.get(field), field, maximum=maximum)
    roster = rules.get("roster")
    if not isinstance(roster, dict) or roster.get("history_pattern") != "history_*.yaml":
        raise ImproperlyConfigured("赛季历史人物来源必须为 history_*.yaml")
    files = roster.get("fiction_files")
    if (
        not isinstance(files, list)
        or not files
        or any(not isinstance(name, str) for name in files)
        or len(files) != len(set(files))
    ):
        raise ImproperlyConfigured("赛季小说人物来源文件列表无效")
    for name in files:
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".yaml")
            or name not in _FICTION_SOURCES
        ):
            raise ImproperlyConfigured("赛季小说人物来源包含不允许的文件")
    frozen = rules.get("faction_roster_keys")
    if frozen is not None:
        _validated_frozen_roster_keys(frozen)
    previous = 0
    scroll_count = 0
    for entry in _entries(rules.get("rewards"), "rewards"):
        threshold = _positive_int(entry.get("threshold"), f"rewards.{entry['key']}.threshold")
        if threshold <= previous:
            raise ImproperlyConfigured("赛季奖励门槛必须严格递增")
        previous = threshold
        if entry.get("kind") == "scroll":
            scroll_count += 1
            if entry.get("items") != {}:
                raise ImproperlyConfigured("结缘卷轴不能同时配置物品奖励")
        elif entry.get("kind") == "items":
            buckets = entry.get("items")
            if not isinstance(buckets, dict) or set(buckets) != set(FACTIONS):
                raise ImproperlyConfigured("赛季物品奖励必须分别配置两个阵营")
            for faction in FACTIONS:
                _item_map(buckets[faction], f"rewards.{entry['key']}.{faction}")
        else:
            raise ImproperlyConfigured("赛季奖励类型无效")
    if scroll_count != 1:
        raise ImproperlyConfigured("赛季必须配置一个结缘卷轴里程碑")
    shop = rules.get("shop")
    if not isinstance(shop, dict) or set(shop) != set(FACTIONS):
        raise ImproperlyConfigured("赛季商店必须分别配置两个阵营")
    for faction in FACTIONS:
        for entry in _entries(shop[faction], f"shop.{faction}"):
            _positive_int(entry.get("cost"), f"shop.{entry['key']}.cost")
            _positive_int(entry.get("limit"), f"shop.{entry['key']}.limit", maximum=100)
            _item_map(entry.get("items"), f"shop.{entry['key']}.items")
    _item_map(rules.get("summon_fallback_items"), "summon_fallback_items")
    return rules


@lru_cache(maxsize=4)
def _load_rules_at(path: str) -> dict[str, Any]:
    rules = load_yaml_data(path, logger=logger, context="赛季规则", default={}, raise_on_error=True)
    return validate_rules(rules)


def load_rules() -> dict[str, Any]:
    """返回独立配置副本，调用方可安全保存为赛季快照。"""
    path = Path(settings.BASE_DIR) / "data" / "season_rules.yaml"
    return copy.deepcopy(_load_rules_at(str(path)))


def _check_faction(faction: str) -> None:
    if faction not in FACTIONS:
        raise ValueError("未知赛季阵营")


def _read_roster_keys(paths: list[Path]) -> set[str]:
    keys: set[str] = set()
    for path in paths:
        data = load_yaml_data(path, logger=logger, context="赛季人物来源", default={}, raise_on_error=True)
        if not isinstance(data, dict):
            raise ImproperlyConfigured(f"赛季人物来源格式无效：{path.name}")
        groups = data.get("heroes") or data.get("hero_roster")
        # 部分既有历史数据以稀有度直接作为根节点；不接受其它未知根结构。
        if groups is None and set(data).issubset(set(GuestRarity.values)):
            groups = data
        if not isinstance(groups, dict):
            raise ImproperlyConfigured(f"赛季人物来源格式无效：{path.name}")
        for entries in groups.values():
            if not isinstance(entries, list):
                raise ImproperlyConfigured(f"赛季人物来源分组格式无效：{path.name}")
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                    keys.add(entry["key"])
    return keys


def _read_history_roster(root: Path, history_pattern: str) -> set[str]:
    history_paths = sorted(root.glob(history_pattern))
    if not history_paths:
        raise ImproperlyConfigured("赛季历史人物来源为空")
    return _read_roster_keys(history_paths)


def _read_faction_rosters(
    base_dir: str, history_pattern: str, fiction_files: tuple[str, ...]
) -> dict[str, frozenset[str]]:
    root = Path(base_dir) / "data" / "guests"
    history = _read_history_roster(root, history_pattern)
    fiction = _read_roster_keys([root / name for name in fiction_files])
    return {"history": frozenset(history), "fiction": frozenset(fiction - history)}


@lru_cache(maxsize=16)
def _faction_keys_at(
    base_dir: str, faction: str, history_pattern: str, fiction_files: tuple[str, ...]
) -> frozenset[str]:
    if faction == "history":
        return frozenset(_read_history_roster(Path(base_dir) / "data" / "guests", history_pattern))
    return _read_faction_rosters(base_dir, history_pattern, fiction_files)[faction]


def faction_template_keys(faction: str, rules: dict[str, Any] | None = None) -> frozenset[str]:
    """以已审核的来源文件划分阵营，同一 key 冲突时历史来源优先。"""
    _check_faction(faction)
    selected_rules = validate_rules(rules) if rules is not None else load_rules()
    frozen = selected_rules.get("faction_roster_keys")
    if frozen is not None:
        return _validated_frozen_roster_keys(frozen)[faction]
    roster = selected_rules["roster"]
    return _faction_keys_at(
        str(settings.BASE_DIR), faction, roster["history_pattern"], tuple(sorted(roster["fiction_files"]))
    )


def build_faction_roster_snapshot(rules: dict[str, Any]) -> dict[str, list[str]]:
    """读取当前来源文件，生成可保存到赛季规则中的完整人物名单。"""
    selected = validate_rules(rules)
    roster = selected["roster"]
    rosters = _read_faction_rosters(
        str(settings.BASE_DIR), roster["history_pattern"], tuple(sorted(roster["fiction_files"]))
    )
    return {faction: sorted(rosters[faction]) for faction in FACTIONS}


def ensure_faction_roster_snapshot_locked(season: Any) -> None:
    """旧赛季在首次需要加成时固定名单；调用方必须持有赛季锁。"""
    if season.rules.get("faction_roster_keys") is not None:
        return
    from gameplay.models.season import Season

    locked = Season.objects.select_for_update().get(pk=season.pk)
    if locked.rules.get("faction_roster_keys") is None:
        locked.rules = {**locked.rules, "faction_roster_keys": build_faction_roster_snapshot(locked.rules)}
        locked.save(update_fields=["rules"])
    season.rules = locked.rules


def _summon_catalog(faction: str, rules: dict[str, Any] | None) -> QuerySet[GuestTemplate]:
    return GuestTemplate.objects.filter(
        key__in=faction_template_keys(faction, rules=rules),
        recruitable=True,
        is_hermit=False,
        is_world_unique=False,
    )


def has_summon_catalog(faction: str, rules: dict[str, Any] | None = None) -> bool:
    """区分人物目录未导入与玩家已经全收集，避免错误开放替代兑换。"""
    return _summon_catalog(faction, rules).exists()


def available_summon_templates(manor: Manor, faction: str, rules: dict[str, Any] | None = None) -> list[GuestTemplate]:
    """仅返回尚未拥有、可正常招募的本阵营人物，不额外限制稀有度。"""
    owned = get_owned_non_repeatable_template_ids(manor.pk)
    owned.update(Guest.objects.filter(manor=manor).values_list("template_id", flat=True))
    return list(_summon_catalog(faction, rules).exclude(pk__in=owned).order_by("name", "key"))


def reward_catalog(faction: str, rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    _check_faction(faction)
    entries = copy.deepcopy((rules if rules is not None else load_rules())["rewards"])
    for entry in entries:
        entry["items"] = entry["items"][faction] if entry["kind"] == "items" else {}
    return entries


def shop_catalog(faction: str, rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    _check_faction(faction)
    return copy.deepcopy((rules if rules is not None else load_rules())["shop"][faction])
