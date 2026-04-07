from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from gameplay.services.technology import get_troop_class_for_key
from trade.selector_builders import _safe_call, record_trade_issue

_TROOP_CATEGORY_LABELS: dict[str, str] = {
    "dao": "刀系",
    "qiang": "枪系",
    "jian": "剑系",
    "quan": "拳系",
    "gong": "弓系",
    "scout": "探子",
    "other": "其他",
}


def _build_troop_bank_categories(available_classes: set[str]) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = [{"key": "all", "name": "全部"}]
    ordered = ["dao", "qiang", "jian", "quan", "gong", "scout", "other"]
    used = {"all"}

    for class_key in ordered:
        if class_key in available_classes:
            categories.append({"key": class_key, "name": _TROOP_CATEGORY_LABELS.get(class_key, class_key)})
            used.add(class_key)

    for class_key in sorted(available_classes):
        if class_key not in used:
            categories.append({"key": class_key, "name": _TROOP_CATEGORY_LABELS.get(class_key, class_key)})
    return categories


def build_bank_trade_context(
    params: Mapping[str, str],
    manor: Any,
    context: dict[str, Any],
    *,
    get_bank_info: Callable[..., Any],
    get_troop_bank_capacity: Callable[..., Any],
    get_troop_bank_used_space: Callable[..., Any],
    get_troop_bank_remaining_space: Callable[..., Any],
    get_troop_bank_rows: Callable[..., Any],
) -> None:
    selected_troop_category = (params.get("troop_category") or "all").strip() or "all"
    manor_id = getattr(manor, "id", None)
    context["bank_info"] = _safe_call(
        get_bank_info,
        manor,
        default={
            "gold_bar_base_price": 0,
            "gold_bar_fee_rate": 0,
            "gold_bar_min_price": 0,
            "gold_bar_max_price": 0,
            "current_rate": 0,
            "next_rate": 0,
            "total_cost_per_bar": 0,
            "supply_factor": 0,
            "progressive_factor": 0,
            "effective_supply": 0,
            "pricing_source": "unavailable",
            "pricing_degraded": True,
            "pricing_status_message": "钱庄汇率数据暂时不可用，已暂时关闭兑换。",
            "exchange_available": False,
            "today_count": 0,
            "manor_silver": getattr(manor, "silver", 0),
        },
        log_message=f"load bank info failed: manor_id={manor_id}",
    )
    if context["bank_info"].get("pricing_degraded"):
        record_trade_issue(
            context,
            section="bank",
            message=context["bank_info"].get("pricing_status_message") or "钱庄部分数据暂时不可用。",
        )

    context["troop_bank_capacity"] = _safe_call(
        get_troop_bank_capacity,
        manor,
        default=5000,
        log_message=f"load troop bank capacity failed: manor_id={manor_id}",
    )
    context["troop_bank_used"], context["troop_bank_remaining"] = _safe_call(
        lambda: (get_troop_bank_used_space(manor), get_troop_bank_remaining_space(manor)),
        default=(0, 5000),
        log_message=f"load troop bank usage failed: manor_id={manor_id}",
    )
    troop_bank_rows = _safe_call(
        get_troop_bank_rows,
        manor,
        default=[],
        log_message=f"load troop bank rows failed: manor_id={manor_id}",
    )

    available_classes: set[str] = set()
    for row in troop_bank_rows:
        troop_key = str(row.get("key") or "").strip()
        troop_class = get_troop_class_for_key(troop_key) or "other"
        row["troop_class"] = troop_class
        available_classes.add(troop_class)

    troop_bank_categories = _build_troop_bank_categories(available_classes)
    valid_category_keys = {item["key"] for item in troop_bank_categories}
    if selected_troop_category not in valid_category_keys:
        selected_troop_category = "all"
    if selected_troop_category != "all":
        troop_bank_rows = [row for row in troop_bank_rows if row.get("troop_class") == selected_troop_category]

    context["troop_bank_rows"] = troop_bank_rows
    context["troop_bank_categories"] = troop_bank_categories
    context["troop_bank_current_category"] = selected_troop_category
