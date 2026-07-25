from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from core.decorators import flash_unexpected_view_error
from core.exceptions import GameError
from core.utils import safe_int, sanitize_error_message
from core.utils.locked_actions import (
    ActionLockSpec,
    acquire_scoped_action_lock,
    build_scoped_action_lock_key,
    release_scoped_action_lock,
)
from gameplay.models import MissionTemplate

logger = logging.getLogger(__name__)

MISSION_CARD_KEY = "mission_card"
MISSION_ACTION_LOCK_SECONDS = 5
MISSION_ACTION_LOCK_NAMESPACE = "mission:view_lock"
MISSION_ACTION_LOCK_SPEC = ActionLockSpec(
    namespace=MISSION_ACTION_LOCK_NAMESPACE,
    timeout_seconds=MISSION_ACTION_LOCK_SECONDS,
    logger=logger,
    log_context="mission action lock",
)


def handle_unexpected_mission_error(
    request: HttpRequest,
    exc: Exception,
    *,
    log_message: str,
    log_args: tuple[object, ...],
    logger_instance: logging.Logger | None = None,
) -> None:
    flash_unexpected_view_error(
        request,
        exc,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger_instance or logger,
    )


def handle_known_mission_error(request: HttpRequest, exc: GameError) -> None:
    messages.error(request, sanitize_error_message(exc))


def normalize_mission_key(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    mission_key = str(raw_value).strip()
    return mission_key or None


def mission_tasks_url(mission_key: str | None = None) -> str:
    base_url = reverse("gameplay:tasks")
    if mission_key:
        return f"{base_url}?mission={mission_key}"
    return base_url


def mission_tasks_redirect(mission_key: str | None = None) -> HttpResponse:
    return redirect(mission_tasks_url(mission_key))


def _normalized_available_weekdays(mission: MissionTemplate) -> set[int]:
    raw_weekdays = getattr(mission, "available_weekdays", None)
    if not isinstance(raw_weekdays, list):
        return set()

    weekdays: set[int] = set()
    for raw_weekday in raw_weekdays:
        if isinstance(raw_weekday, bool):
            continue
        try:
            weekday = int(raw_weekday)
        except (TypeError, ValueError):
            continue
        if 1 <= weekday <= 7:
            weekdays.add(weekday)
    return weekdays


def is_mission_available_on(mission: MissionTemplate, current_date: date | None = None) -> bool:
    weekdays = _normalized_available_weekdays(mission)
    if not weekdays:
        return True
    today = current_date or timezone.localdate()
    return today.isoweekday() in weekdays


def filter_available_missions(
    missions: list[MissionTemplate], current_date: date | None = None
) -> list[MissionTemplate]:
    today = current_date or timezone.localdate()
    return [mission for mission in missions if is_mission_available_on(mission, today)]


def resolve_mission_or_redirect(
    request: HttpRequest, mission_key_raw: Any
) -> tuple[MissionTemplate | None, HttpResponse | None]:
    mission_key = normalize_mission_key(mission_key_raw)
    if mission_key is None:
        messages.error(request, "请选择任务")
        return None, mission_tasks_redirect()

    mission = MissionTemplate.objects.filter(key=mission_key).first()
    if mission is None:
        messages.error(request, "任务不存在")
        return None, mission_tasks_redirect(mission_key)

    if not is_mission_available_on(mission):
        messages.error(request, "该任务今日未开放")
        return None, mission_tasks_redirect()

    return mission, None


def parse_positive_ids(raw_values: list[str]) -> list[int] | None:
    if not raw_values:
        return []

    parsed: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        value = safe_int(raw, default=None)
        if value is None or value <= 0:
            return None
        if value in seen:
            continue
        parsed.append(value)
        seen.add(value)
    return parsed


def mission_action_lock_key(action: str, manor_id: int, scope: str) -> str:
    return build_scoped_action_lock_key(MISSION_ACTION_LOCK_SPEC, action, manor_id, scope)


def acquire_mission_action_lock(action: str, manor_id: int, scope: str) -> tuple[bool, str, str | None]:
    return acquire_scoped_action_lock(MISSION_ACTION_LOCK_SPEC, action, manor_id, scope)


def release_mission_action_lock(lock_key: str, lock_token: str | None) -> None:
    release_scoped_action_lock(MISSION_ACTION_LOCK_SPEC, lock_key, lock_token)


def collect_mission_asset_keys(missions: list[MissionTemplate]) -> tuple[set[str], set[str], set[str]]:
    enemy_keys: set[str] = set()
    troop_keys: set[str] = set()
    drop_keys: set[str] = set()

    for mission in missions:
        enemy_guests = getattr(mission, "enemy_guests", None)
        if enemy_guests is None:
            enemy_entries: list[Any] = []
        elif isinstance(enemy_guests, list):
            enemy_entries = enemy_guests
        else:
            raise AssertionError(f"invalid mission enemy_guests: {enemy_guests!r}")

        for entry in enemy_entries:
            if isinstance(entry, str):
                key = entry.strip()
                if not key:
                    raise AssertionError(f"invalid mission enemy_guests entry: {entry!r}")
                enemy_keys.add(key)
            elif isinstance(entry, dict):
                raw_key = entry.get("key")
                if not isinstance(raw_key, str) or not raw_key.strip():
                    raise AssertionError(f"invalid mission enemy_guests entry: {entry!r}")
                enemy_keys.add(raw_key.strip())
            else:
                raise AssertionError(f"invalid mission enemy_guests entry: {entry!r}")

        enemy_troops = getattr(mission, "enemy_troops", None)
        if enemy_troops is None:
            normalized_enemy_troops: dict[str, Any] = {}
        elif isinstance(enemy_troops, dict):
            normalized_enemy_troops = enemy_troops
        else:
            raise AssertionError(f"invalid mission enemy_troops: {enemy_troops!r}")

        drop_table = getattr(mission, "drop_table", None)
        if drop_table is None:
            normalized_drop_table: dict[str, Any] = {}
        elif isinstance(drop_table, dict):
            normalized_drop_table = drop_table
        else:
            raise AssertionError(f"invalid mission drop_table: {drop_table!r}")

        probability_drop_table = getattr(mission, "probability_drop_table", None)
        if probability_drop_table is None:
            normalized_probability_drop_table: dict[str, Any] = {}
        elif isinstance(probability_drop_table, dict):
            normalized_probability_drop_table = probability_drop_table
        else:
            raise AssertionError(f"invalid mission probability_drop_table: {probability_drop_table!r}")

        entry_cost = getattr(mission, "entry_cost", None)
        if entry_cost is None:
            normalized_entry_cost: dict[str, Any] = {}
        elif isinstance(entry_cost, dict):
            normalized_entry_cost = entry_cost
        else:
            raise AssertionError(f"invalid mission entry_cost: {entry_cost!r}")

        for key in normalized_enemy_troops.keys():
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid mission enemy_troops key: {key!r}")
            troop_keys.add(key.strip())
        for key in normalized_drop_table.keys():
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid mission drop_table key: {key!r}")
            drop_keys.add(key.strip())
        for key in normalized_probability_drop_table.keys():
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid mission probability_drop_table key: {key!r}")
            drop_keys.add(key.strip())
        for key in normalized_entry_cost.keys():
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid mission entry_cost key: {key!r}")
            drop_keys.add(key.strip())
        for drop_value in normalized_drop_table.values():
            if not isinstance(drop_value, dict):
                continue
            choices = drop_value.get("choices")
            if choices is None:
                continue
            for choice_key in iter_choice_pool_keys(drop_value):
                drop_keys.add(choice_key)

    return enemy_keys, troop_keys, drop_keys


def parse_drop_value(value: Any) -> tuple[float | None, int | None]:
    chance = None
    count = None
    if isinstance(value, dict):
        raw_chance = value.get("chance", value.get("probability"))
        raw_count = value.get("count", value.get("quantity", value.get("amount")))
        if raw_chance is not None:
            if isinstance(raw_chance, bool):
                raise AssertionError(f"invalid mission drop chance: {raw_chance!r}")
            try:
                chance = float(raw_chance)
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"invalid mission drop chance: {raw_chance!r}") from exc
        if raw_count is not None:
            if isinstance(raw_count, bool):
                raise AssertionError(f"invalid mission drop count: {raw_count!r}")
            try:
                count = int(raw_count)
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"invalid mission drop count: {raw_count!r}") from exc
    else:
        if isinstance(value, bool):
            raise AssertionError(f"invalid mission drop value: {value!r}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid mission drop value: {value!r}") from exc
        if number is not None and 0 < number < 1:
            chance = number
            count = 1
        elif number is not None and number >= 1:
            count = int(number)

    if chance is not None and count is None:
        count = 1
    return chance, count


def resolve_drop_label(
    key: str,
    drop_labels: dict[str, str],
    item_templates: dict[str, Any],
    book_labels: dict[str, str],
) -> str:
    label = drop_labels.get(key)
    if label:
        return label
    tpl = item_templates.get(key)
    if tpl:
        return tpl.name
    if key in book_labels:
        return book_labels[key]
    return "未知奖励"


def resolve_drop_pool_label(
    value: Any,
    drop_labels: dict[str, str],
    item_templates: dict[str, Any],
    book_labels: dict[str, str],
) -> str | None:
    if not isinstance(value, dict):
        return None
    choice_keys = iter_choice_pool_keys(value)
    if not choice_keys:
        return None

    labels: list[str] = []
    for choice_key in choice_keys:
        labels.append(resolve_drop_label(choice_key, drop_labels, item_templates, book_labels))

    if not labels:
        return None
    return "/".join(labels)


def resolve_drop_pool_rarity(value: Any, loot_rarities: dict[str, str]) -> str | None:
    if not isinstance(value, dict):
        return None
    choice_keys = iter_choice_pool_keys(value)
    if not choice_keys:
        return None

    rarities: list[str] = []
    for choice_key in choice_keys:
        rarity = loot_rarities.get(choice_key)
        if rarity:
            rarities.append(rarity)

    if not rarities:
        return None
    if len(set(rarities)) == 1:
        return rarities[0]
    return "default"


def iter_choice_pool_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    choices = value.get("choices")
    if choices is None:
        return []
    if not isinstance(choices, list):
        raise AssertionError(f"invalid mission drop choices: {choices!r}")

    keys: list[str] = []
    for choice in choices:
        if isinstance(choice, str):
            choice_key = choice.strip()
        elif isinstance(choice, dict):
            choice_key = str(choice.get("key") or choice.get("item_key") or choice.get("template_key") or "").strip()
        else:
            raise AssertionError(f"invalid mission drop choice entry: {choice!r}")
        if not choice_key:
            raise AssertionError(f"invalid mission drop choice entry: {choice!r}")
        keys.append(choice_key)
    return keys


def build_drop_display_item(
    *,
    key: str,
    name: str,
    label: str,
    count: int | None,
    rarity: str,
    item_templates: dict[str, Any],
) -> dict[str, Any]:
    template = item_templates.get(key)
    image = getattr(template, "image", None)
    return {
        "key": key,
        "name": name,
        "label": label,
        "count": count,
        "rarity": rarity,
        "image_url": image.url if image else "",
    }


def build_drop_lists(
    selected_mission: MissionTemplate,
    drop_labels: dict[str, str],
    item_templates: dict[str, Any],
    book_labels: dict[str, str],
    loot_rarities: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    guaranteed_drops: list[dict[str, Any]] = []
    probability_drops: list[dict[str, Any]] = []
    handled_probability_keys: set[str] = set()
    raw_drop_table = getattr(selected_mission, "drop_table", None)
    if raw_drop_table is None:
        drop_table: dict[str, Any] = {}
    elif isinstance(raw_drop_table, dict):
        drop_table = {}
        for key, value in raw_drop_table.items():
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid mission drop_table key: {key!r}")
            drop_table[key.strip()] = value
    else:
        raise AssertionError(f"invalid mission drop_table: {raw_drop_table!r}")

    raw_probability_drop_table = getattr(selected_mission, "probability_drop_table", None)
    if raw_probability_drop_table is None:
        probability_drop_table: dict[str, Any] = {}
    elif isinstance(raw_probability_drop_table, dict):
        probability_drop_table = {}
        for key, value in raw_probability_drop_table.items():
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid mission probability_drop_table key: {key!r}")
            probability_drop_table[key.strip()] = value
    else:
        raise AssertionError(f"invalid mission probability_drop_table: {raw_probability_drop_table!r}")

    for key, val in drop_table.items():
        if isinstance(val, dict) and val.get("choices") and probability_drop_table:
            for choice_key in iter_choice_pool_keys(val):
                if choice_key not in probability_drop_table:
                    continue
                handled_probability_keys.add(choice_key)
                label = resolve_drop_label(choice_key, drop_labels, item_templates, book_labels)
                _, count = parse_drop_value(probability_drop_table.get(choice_key))
                rarity = loot_rarities.get(choice_key) or "default"
                display_label = f"{label} ×{count}" if (count is not None and count >= 1) else label
                probability_drops.append(
                    build_drop_display_item(
                        key=choice_key,
                        name=label,
                        label=display_label,
                        count=count,
                        rarity=rarity,
                        item_templates=item_templates,
                    )
                )
            continue
        label = resolve_drop_pool_label(val, drop_labels, item_templates, book_labels) or resolve_drop_label(
            key,
            drop_labels,
            item_templates,
            book_labels,
        )
        chance, count = parse_drop_value(val)
        rarity = resolve_drop_pool_rarity(val, loot_rarities) or loot_rarities.get(key) or "default"

        is_choice_pool = resolve_drop_pool_label(val, drop_labels, item_templates, book_labels) is not None
        choice_keys = iter_choice_pool_keys(val) if is_choice_pool else []
        display_key = choice_keys[0] if len(choice_keys) == 1 else key
        if count is not None and count >= 1 and not (is_choice_pool and count == 1):
            display_label = f"{label} ×{count}"
        else:
            display_label = label
        drop_item = build_drop_display_item(
            key=display_key,
            name=label,
            label=display_label,
            count=count,
            rarity=rarity,
            item_templates=item_templates,
        )
        if chance is not None and 0 < chance < 1:
            probability_drops.append(drop_item)
        else:
            guaranteed_drops.append(drop_item)

    for key, val in probability_drop_table.items():
        if key in handled_probability_keys:
            continue
        label = resolve_drop_label(key, drop_labels, item_templates, book_labels)
        _, count = parse_drop_value(val)
        display_label = f"{label} ×{count}" if (count is not None and count >= 1) else label
        rarity = loot_rarities.get(key) or "default"
        probability_drops.append(
            build_drop_display_item(
                key=key,
                name=label,
                label=display_label,
                count=count,
                rarity=rarity,
                item_templates=item_templates,
            )
        )

    preview_keys: set[str] = set()
    unique_guaranteed_drops: list[dict[str, Any]] = []
    unique_probability_drops: list[dict[str, Any]] = []
    for drops, unique_drops in (
        (guaranteed_drops, unique_guaranteed_drops),
        (probability_drops, unique_probability_drops),
    ):
        for drop in drops:
            if drop["key"] in preview_keys:
                continue
            preview_keys.add(drop["key"])
            unique_drops.append(drop)

    return unique_guaranteed_drops, unique_probability_drops


def build_entry_cost_list(
    selected_mission: MissionTemplate,
    item_templates: dict[str, Any],
    manor: Any,
    *,
    get_item_quantity_func,
) -> tuple[list[dict[str, Any]], bool]:
    raw_entry_cost = getattr(selected_mission, "entry_cost", None)
    if raw_entry_cost is None:
        entry_cost: dict[str, Any] = {}
    elif isinstance(raw_entry_cost, dict):
        entry_cost = raw_entry_cost
    else:
        raise AssertionError(f"invalid mission entry_cost: {raw_entry_cost!r}")

    entries: list[dict[str, Any]] = []
    can_afford = True
    for key, raw_amount in entry_cost.items():
        if not isinstance(key, str) or not key.strip():
            raise AssertionError(f"invalid mission entry_cost key: {key!r}")
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, int) or raw_amount <= 0:
            raise AssertionError(f"invalid mission entry_cost amount: {raw_amount!r}")

        item_key = key.strip()
        template = item_templates.get(item_key)
        owned = get_item_quantity_func(manor, item_key)
        missing = owned < raw_amount
        can_afford = can_afford and not missing
        entries.append(
            {
                "key": item_key,
                "label": getattr(template, "name", "未知物品"),
                "required": raw_amount,
                "owned": owned,
                "rarity": getattr(template, "rarity", "default") or "default",
                "missing": missing,
            }
        )
    return entries, can_afford


def build_mission_data(
    missions: list[MissionTemplate],
    attempts: dict[str, int],
    extra_attempts: dict[str, int],
) -> list[dict[str, Any]]:
    mission_data: list[dict[str, Any]] = []
    for mission in missions:
        used = attempts.get(mission.key, 0)
        extra = extra_attempts.get(mission.key, 0)
        daily_limit = mission.daily_limit + extra
        remaining = max(0, daily_limit - used)
        mission_data.append(
            {
                "mission": mission,
                "used": used,
                "remaining": remaining,
                "daily_limit": daily_limit,
                "extra": extra,
            }
        )
    return mission_data


def build_selection_summary(
    selected_key: str | None,
    missions_by_key: dict[str, MissionTemplate],
    attempts: dict[str, int],
    extra_attempts: dict[str, int],
) -> tuple[MissionTemplate | None, int, int, int]:
    selected_mission = missions_by_key.get(selected_key) if selected_key else None
    selected_attempts = attempts.get(selected_key, 0) if selected_key else 0
    selected_extra = extra_attempts.get(selected_key, 0) if selected_key else 0
    selected_daily_limit = (selected_mission.daily_limit + selected_extra) if selected_mission else 0
    selected_remaining = max(0, selected_daily_limit - selected_attempts)
    return selected_mission, selected_attempts, selected_daily_limit, selected_remaining


def build_troop_config() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    from battle.troops import load_troop_templates

    troop_templates = load_troop_templates()
    troop_template_items = sorted(
        troop_templates.items(),
        key=lambda item: safe_int(item[1].get("priority"), default=0) or 0,
    )
    config_items = [
        {
            "key": key,
            "label": data.get("label", "未知兵种"),
            "description": data.get("description", "") or "",
            "value": 0,
        }
        for key, data in troop_template_items
    ]
    return troop_templates, config_items
