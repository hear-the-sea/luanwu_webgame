"""按门客升阶前后的模板关系识别不可重复获取的人物。"""

from __future__ import annotations

from collections.abc import Iterable

from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX, UPGRADE_TEMPLATE_FAMILIES
from guests.models import Guest, GuestRarity, GuestTemplate

from .recruitment_shared import NON_REPEATABLE_RARITIES

_UPGRADE_FAMILY_BY_KEY = {key: family for family in UPGRADE_TEMPLATE_FAMILIES for key in family}
_RARITY_VALUES = {rarity for rarity, _label in GuestRarity.choices}


def _conversion_source_template_id(template_key: str) -> int | None:
    if not template_key.startswith(RARITY_CONVERSION_TEMPLATE_KEY_PREFIX):
        return None
    suffix = template_key[len(RARITY_CONVERSION_TEMPLATE_KEY_PREFIX) :]
    rarity, separator, source_id_text = suffix.rpartition("_")
    if not separator or rarity not in _RARITY_VALUES or not source_id_text.isdecimal():
        return None
    source_id = int(source_id_text)
    return source_id if source_id > 0 else None


def _is_non_repeatable(rarity: str, is_hermit: bool) -> bool:
    return rarity in NON_REPEATABLE_RARITIES or (rarity == GuestRarity.BLACK and is_hermit)


def get_non_repeatable_family_template_ids_by_id(template_ids: Iterable[int]) -> dict[int, set[int]]:
    """批量展开各模板的人物关系，避免逐候选查询升阶来源和版本。"""
    root_ids = {int(template_id) for template_id in template_ids}
    pending_ids = root_ids.copy()
    visited_ids: set[int] = set()
    family_keys: set[str] = set()
    template_rows: dict[int, tuple[str, str, bool]] = {}

    while pending_ids:
        current_ids = pending_ids - visited_ids
        if not current_ids:
            break
        visited_ids.update(current_ids)
        pending_ids = set()
        rows = GuestTemplate.objects.filter(pk__in=current_ids).values_list("pk", "key", "rarity", "is_hermit")
        for template_id, key, rarity, is_hermit in rows:
            template_rows[template_id] = (key, rarity, is_hermit)
            if _is_non_repeatable(rarity, is_hermit):
                family_keys.update(_UPGRADE_FAMILY_BY_KEY.get(key, ()))
            source_id = _conversion_source_template_id(key)
            if source_id is not None and source_id not in visited_ids:
                pending_ids.add(source_id)

    variants_by_key: dict[str, int] = {}
    if family_keys:
        variants = GuestTemplate.objects.filter(key__in=family_keys).values_list("pk", "key", "rarity", "is_hermit")
        variants_by_key = {
            key: template_id
            for template_id, key, rarity, is_hermit in variants
            if _is_non_repeatable(rarity, is_hermit)
        }

    families: dict[int, set[int]] = {}
    for root_id in root_ids:
        excluded_ids: set[int] = set()
        seen: set[int] = set()
        current_id: int | None = root_id
        while current_id is not None and current_id not in seen and current_id in template_rows:
            seen.add(current_id)
            key, rarity, is_hermit = template_rows[current_id]
            if _is_non_repeatable(rarity, is_hermit):
                excluded_ids.add(current_id)
                excluded_ids.update(
                    variants_by_key[variant_key]
                    for variant_key in _UPGRADE_FAMILY_BY_KEY.get(key, ())
                    if variant_key in variants_by_key
                )
            current_id = _conversion_source_template_id(key)
        families[root_id] = excluded_ids
    return families


def get_non_repeatable_family_template_ids(template_ids: Iterable[int]) -> set[int]:
    """展开不可重复模板、动态升阶来源及显式配置的升阶版本。"""
    families = get_non_repeatable_family_template_ids_by_id(template_ids)
    return set().union(*families.values())


def get_owned_non_repeatable_template_ids(manor_id: int) -> set[int]:
    owned_ids = Guest.objects.filter(manor_id=manor_id).values_list("template_id", flat=True)
    return get_non_repeatable_family_template_ids(owned_ids)
