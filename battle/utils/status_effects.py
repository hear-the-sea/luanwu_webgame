"""
状态效果工具模块

管理战斗中的状态效果（如眩晕、中毒等）及其生命周期。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


# 状态效果定义
STATUS_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "stunned": {
        "label": "眩晕",
        "message": "眩晕中",
        "skip_action": True,
    },
    "weakened": {
        "label": "士气低落",
        "message": "士气低落",
        "skip_action": False,
        "damage_reduction": 0.3,  # 伤害降低30%
    },
}


# 控制类状态列表（对小兵转换为削弱效果）
CONTROL_STATUS_EFFECTS = {"stunned"}


def cleanup_status_effects(combatant: "Combatant") -> None:
    """
    清理无效的状态效果。

    如果active和pending计数器都归零，则移除该状态。

    Args:
        combatant: 战斗单位
    """
    for status in list(combatant.status_effects.keys()):
        payload = combatant.status_effects.get(status) or {}
        active = max(0, int(payload.get("active", 0)))
        pending = max(0, int(payload.get("pending", 0)))
        if active <= 0 and pending <= 0:
            combatant.status_effects.pop(status, None)
        else:
            combatant.status_effects[status] = {"active": active, "pending": pending}


def apply_status_effect(target: "Combatant", status: str, duration: int, defer: bool) -> None:
    """
    对目标施加状态效果。

    Args:
        target: 目标单位
        status: 状态key
        duration: 持续回合数
        defer: 是否延迟生效（如果目标本回合已行动，则存入pending）
    """
    if not status or duration <= 0:
        return
    payload = target.status_effects.setdefault(status, {"active": 0, "pending": 0})
    if defer:
        payload["pending"] = max(payload.get("pending", 0), duration)
    else:
        payload["active"] = max(payload.get("active", 0), duration)
    cleanup_status_effects(target)


def get_status_label(status: str) -> str:
    """获取状态的显示名称"""
    definition = STATUS_DEFINITIONS.get(status, {})
    return definition.get("label", status)


def get_status_message(status: str) -> str:
    """获取状态生效时的提示文本"""
    definition = STATUS_DEFINITIONS.get(status, {})
    return definition.get("message", "状态效果发动中")


def get_damage_penalty(actor: "Combatant") -> float:
    """
    获取状态效果导致的伤害惩罚比例。

    Args:
        actor: 攻击单位

    Returns:
        伤害降低比例（0.0 ~ 1.0），0表示无惩罚，0.3表示降低30%
    """
    total_penalty = 0.0
    for status, payload in actor.status_effects.items():
        active = payload.get("active", 0)
        if active <= 0:
            continue
        definition = STATUS_DEFINITIONS.get(status, {})
        penalty = definition.get("damage_reduction", 0)
        if penalty > 0:
            total_penalty += penalty
    return min(0.9, total_penalty)  # 最多降低90%


def capture_active_status_effects(actor: "Combatant") -> Dict[str, int]:
    """Capture statuses that were active when an action opportunity began."""

    captured: Dict[str, int] = {}
    for status, payload in actor.status_effects.items():
        active = max(0, int((payload or {}).get("active", 0)))
        if active > 0:
            captured[status] = active
    return captured


def consume_active_status_effects(actor: "Combatant", active_at_start: Dict[str, int]) -> None:
    """Consume each status active at action start exactly once."""

    for status in list(active_at_start):
        payload = actor.status_effects.get(status)
        if not payload:
            continue
        payload["active"] = max(0, int(payload.get("active", 0)) - 1)
    active_at_start.clear()
    cleanup_status_effects(actor)


def handle_pre_action_status(actor: "Combatant", events: List[Dict[str, Any]]) -> bool:
    """
    处理行动前的状态效果（如眩晕跳过回合）。

    Args:
        actor: 行动单位
        events: 战斗事件列表（用于记录状态生效日志）

    Returns:
        如果行动被阻止返回 True，否则返回 False
    """
    for status, payload in list(actor.status_effects.items()):
        active = payload.get("active", 0)
        if active <= 0:
            continue
        definition = STATUS_DEFINITIONS.get(status)
        if not definition or not definition.get("skip_action"):
            continue

        actor.has_acted_this_round = True
        actor.last_round_acted = actor.current_round

        events.append(
            {
                "actor": actor.name,
                "side": actor.side,
                "status": status,
                "message": get_status_message(status),
                "order": len(events) + 1,
                "kind": actor.kind,
                "priority": actor.priority,
            }
        )
        return True
    return False
