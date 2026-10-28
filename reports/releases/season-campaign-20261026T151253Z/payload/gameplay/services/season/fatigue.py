"""车轮战疲敝只作用于临时战斗快照，不改变真实属性或生命上限。"""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from guests.guest_rules import compute_guest_attack_attribute_component


def fatigue_rules(run: Any) -> dict[str, int]:
    config = run.member.season.rules.get("campaign", {}).get("fortifications") or {}
    if not config.get("enabled"):
        return {}
    return dict(config.get("fatigue") or {})


def fatigue_snapshot(run: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(snapshot)
    rules = fatigue_rules(run)
    if not rules:
        return result
    stacks = min(rules["max_stacks"], max(0, int(run.result.get("fatigue_stacks", 0))))
    if not stacks:
        return result
    percent = 100 - rules["per_stack_percent"] * stacks
    original = SimpleNamespace(**snapshot)
    old_component = snapshot.get("attack_attribute_component")
    if old_component is None:
        old_component = compute_guest_attack_attribute_component(original)
    attack_bonus = snapshot["attack"] - old_component
    for key in ("force", "intellect", "agility", "defense_stat"):
        result[key] = max(0, int(snapshot.get(key, 0) * percent // 100))
    result["attack_attribute_component"] = compute_guest_attack_attribute_component(SimpleNamespace(**result))
    result["attack"] = max(1, result["attack_attribute_component"] + attack_bonus)
    result["defense"] = max(1, int(snapshot["defense"] * percent // 100))
    # 防御属性下降不扣除生命；沿用出征上限与真实剩余生命。
    return result


def award_fatigue_locked(run: Any, *, won: bool, survivor_ids: set[int]) -> None:
    rules = fatigue_rules(run)
    if not rules or not won or not survivor_ids:
        return
    stacks = min(rules["max_stacks"], max(0, int(run.result.get("fatigue_stacks", 0))) + 1)
    run.result = {**run.result, "fatigue_stacks": stacks}


def fatigue_report_effects(runs: list[Any]) -> list[dict[str, Any]]:
    effects = []
    for run in runs:
        rules = fatigue_rules(run)
        if not rules:
            continue
        stacks = min(rules["max_stacks"], max(0, int(run.result.get("fatigue_stacks", 0))))
        effects.append(
            {
                "run_id": run.pk,
                "name": run.member.manor.display_name,
                "fatigue_stacks": stacks,
                "fatigue_percent": stacks * rules["per_stack_percent"],
                "label": f"疲敝{stacks}层，武力、智力、敏捷、防御降低{stacks * rules['per_stack_percent']}%，生命保持不变",
            }
        )
    return effects
