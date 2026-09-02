from __future__ import annotations

import logging
import random
from typing import Dict, List, Tuple

from django.core.cache import cache

from core.exceptions import NoTemplateAvailableError
from gameplay.services.utils.cache import CACHE_TIMEOUT_CONFIG, CacheKeys

from ..models import GuestRarity, GuestTemplate, RecruitmentPoolEntry
from ..utils.recruitment_utils import HERMIT_RARITY, RARITY_ORDER, choose_rarity, filter_entries, weighted_choice

logger = logging.getLogger(__name__)


def _get_recruitable_templates_by_rarity() -> Dict[str, List[GuestTemplate]]:
    """
    获取所有可招募模板，按稀有度分组缓存。

    使用 Django Cache 缓存结果，支持多进程共享。
    当模板数据变更时，需要调用 clear_template_cache() 刷新。

    Returns:
        按稀有度分组的模板字典
    """
    cache_key = str(CacheKeys.GUEST_TEMPLATES_BY_RARITY)
    cached = cache.get(cache_key)
    if cached is not None:
        cached_template_ids_by_rarity: Dict[str, List[int]] = cached
        all_template_ids = [template_id for ids in cached_template_ids_by_rarity.values() for template_id in ids]
        templates_by_id = {template.id: template for template in GuestTemplate.objects.filter(id__in=all_template_ids)}
        cached_result: Dict[str, List[GuestTemplate]] = {}
        for rarity, ids in cached_template_ids_by_rarity.items():
            cached_result[rarity] = [
                templates_by_id[template_id] for template_id in ids if template_id in templates_by_id
            ]
        return cached_result

    all_templates = list(GuestTemplate.objects.filter(recruitable=True, is_world_unique=False))
    result: Dict[str, List[GuestTemplate]] = {}
    template_ids_by_rarity: Dict[str, List[int]] = {}

    for template in all_templates:
        if template.is_hermit:
            continue

        if template.rarity not in result:
            result[template.rarity] = []
            template_ids_by_rarity[template.rarity] = []
        result[template.rarity].append(template)
        template_ids_by_rarity[template.rarity].append(template.id)

    cache.set(cache_key, template_ids_by_rarity, timeout=int(CACHE_TIMEOUT_CONFIG))
    return result


def _get_hermit_templates() -> List[GuestTemplate]:
    """
    获取所有可招募的隐士模板（缓存）。

    Returns:
        隐士模板列表
    """
    cache_key = str(CacheKeys.HERMIT_TEMPLATES)
    cached = cache.get(cache_key)
    if cached is not None:
        return list(GuestTemplate.objects.filter(id__in=cached))

    templates = list(
        GuestTemplate.objects.filter(
            rarity=GuestRarity.BLACK,
            is_hermit=True,
            recruitable=True,
        )
    )
    template_ids = [template.id for template in templates]
    cache.set(cache_key, template_ids, timeout=int(CACHE_TIMEOUT_CONFIG))
    return templates


def clear_template_cache() -> None:
    """
    清除模板缓存。

    当 GuestTemplate 数据变更时调用此函数刷新缓存。
    """
    cache.delete_many(
        [
            str(CacheKeys.GUEST_TEMPLATES_BY_RARITY),
            str(CacheKeys.HERMIT_TEMPLATES),
        ]
    )


def _filter_templates(templates: List[GuestTemplate], excluded_ids: set[int]) -> List[GuestTemplate]:
    """从模板列表中过滤掉已排除的模板"""
    if not excluded_ids:
        return templates
    return [template for template in templates if template.id not in excluded_ids]


def _build_rarity_search_order(rarity: str) -> list[str]:
    search_order = [rarity]
    if rarity in RARITY_ORDER:
        idx = RARITY_ORDER.index(rarity)
        search_order.extend(RARITY_ORDER[idx + 1 :] + RARITY_ORDER[:idx])
    else:
        search_order.extend(RARITY_ORDER)
    return search_order


def _resolve_entry_template(
    entry: RecruitmentPoolEntry,
    rarity_hint: str,
    excluded_ids: set[int],
    explicit_template_ids: set[int],
    templates_by_rarity: Dict[str, List[GuestTemplate]],
    category_cache: Dict[Tuple[str | None, str | None], List[GuestTemplate]],
    rng: random.Random,
) -> GuestTemplate | None:
    if entry.template_id:
        template = entry.template
        if template is None:
            raise AssertionError(f"invalid recruitment pool entry template: {entry.template_id!r}")
        if not template.recruitable or getattr(template, "is_world_unique", False):
            raise AssertionError(f"invalid recruitment pool entry template: {entry.template_id!r}")
        return template

    rarity_value = entry.rarity or rarity_hint
    if not rarity_value:
        raise AssertionError("invalid recruitment pool entry rarity: None")
    archetype_key = entry.archetype or None
    cache_key = (rarity_value, archetype_key)
    if cache_key not in category_cache:
        base_templates = templates_by_rarity.get(rarity_value, [])
        if archetype_key:
            base_templates = [template for template in base_templates if template.archetype == archetype_key]
        category_cache[cache_key] = _filter_templates(base_templates, explicit_template_ids | excluded_ids)
    templates = category_cache[cache_key]
    return rng.choice(templates) if templates else None


def choose_template_from_entries(
    entries: List[RecruitmentPoolEntry],
    rng: random.Random,
    excluded_ids: set[int] | None = None,
    *,
    templates_by_rarity: Dict[str, List[GuestTemplate]] | None = None,
    hermit_templates: List[GuestTemplate] | None = None,
    rarity: str | None = None,
) -> GuestTemplate:
    """
    从卡池条目中随机选择一个门客模板。

    如果 entries 为空，直接从所有 recruitable=True 的模板中按稀有度随机选择。
    """
    if excluded_ids is None:
        excluded_ids = set()

    rarity = choose_rarity(rng) if rarity is None else str(rarity)

    if rarity == HERMIT_RARITY:
        loaded_hermit_templates = hermit_templates if hermit_templates is not None else _get_hermit_templates()
        available_hermit_templates = _filter_templates(loaded_hermit_templates, excluded_ids)
        if available_hermit_templates:
            return rng.choice(available_hermit_templates)
        rarity = GuestRarity.BLACK

    if not entries:
        return _choose_template_by_rarity_cached(
            rarity,
            excluded_ids,
            rng,
            templates_by_rarity=templates_by_rarity,
        )

    filtered_entries = [entry for entry in entries if not entry.template_id or entry.template_id not in excluded_ids]
    for entry in filtered_entries:
        if not entry.template_id and not getattr(entry, "rarity", None):
            raise AssertionError("invalid recruitment pool entry rarity: None")
    explicit_template_ids = {entry.template_id for entry in filtered_entries if entry.template_id}
    loaded_templates_by_rarity = (
        templates_by_rarity if templates_by_rarity is not None else _get_recruitable_templates_by_rarity()
    )
    category_cache: Dict[Tuple[str | None, str | None], List[GuestTemplate]] = {}
    search_order = _build_rarity_search_order(rarity)

    for rarity_option in search_order:
        options = filter_entries(filtered_entries, rarity_option)
        if not options:
            continue
        chosen_entry = weighted_choice(options, rng)
        template = _resolve_entry_template(
            chosen_entry,
            rarity_option,
            excluded_ids,
            explicit_template_ids,
            loaded_templates_by_rarity,
            category_cache,
            rng,
        )
        if template:
            return template

    return _choose_template_by_rarity_cached(
        rarity,
        excluded_ids,
        rng,
        templates_by_rarity=loaded_templates_by_rarity,
    )


def _choose_template_by_rarity_cached(
    rarity: str,
    excluded_ids: set[int],
    rng: random.Random,
    *,
    templates_by_rarity: Dict[str, List[GuestTemplate]] | None = None,
) -> GuestTemplate:
    """
    按稀有度从缓存的模板中随机选择。

    使用预加载的模板缓存，避免数据库查询。
    如果目标稀有度无可用模板，会尝试降级到其他稀有度。
    """
    loaded_templates_by_rarity = (
        templates_by_rarity if templates_by_rarity is not None else _get_recruitable_templates_by_rarity()
    )

    for rarity_option in _build_rarity_search_order(rarity):
        templates = loaded_templates_by_rarity.get(rarity_option, [])
        available = _filter_templates(templates, excluded_ids)
        if available:
            return rng.choice(available)

    raise NoTemplateAvailableError()


def _choose_template_by_rarity(
    rarity: str,
    excluded_ids: set[int],
    rng: random.Random,
) -> GuestTemplate:
    """
    按稀有度从所有可招募模板中随机选择。

    如果目标稀有度无可用模板，会尝试降级到其他稀有度。
    """
    for rarity_option in _build_rarity_search_order(rarity):
        queryset = GuestTemplate.objects.filter(rarity=rarity_option, recruitable=True)
        queryset = queryset.filter(is_world_unique=False)
        if rarity_option == GuestRarity.BLACK:
            queryset = queryset.filter(is_hermit=False)
        if excluded_ids:
            queryset = queryset.exclude(id__in=excluded_ids)
        templates = list(queryset)
        if templates:
            return rng.choice(templates)

    raise NoTemplateAvailableError()
