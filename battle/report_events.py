from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any


def _iter_nested_damage_events(events: Iterable[object]) -> Iterator[Mapping[str, Any]]:
    for event in events:
        if not isinstance(event, Mapping):
            continue
        yield event
        additional_targets = event.get("additional_targets")
        if isinstance(additional_targets, list):
            yield from _iter_nested_damage_events(additional_targets)
        additional_attacks = event.get("additional_attacks")
        if isinstance(additional_attacks, list):
            yield from _iter_nested_damage_events(additional_attacks)


def iter_damage_events(rounds: Iterable[object] | None) -> Iterator[Mapping[str, Any]]:
    """Yield all attack hits, including secondary targets and troop attacks."""

    for battle_round in rounds or []:
        if not isinstance(battle_round, Mapping):
            continue
        events = battle_round.get("events")
        if isinstance(events, list):
            yield from _iter_nested_damage_events(events)


def troop_phase_skill(attack: object, action: object = None, round_priority: object = None) -> str:
    """Identify troop technology attacks; follow-up hits inherit the action phase."""
    if not isinstance(attack, Mapping) or attack.get("current_target_bonus"):
        return ""
    action = action if isinstance(action, Mapping) else attack
    if attack.get("kind", action.get("kind")) != "troop":
        return ""
    actor_key = attack.get("actor_template_key", action.get("actor_template_key"))
    actor_name = attack.get("actor", action.get("actor"))
    # 铁骑自带先攻，不属于剑系科技；名称兼容未记录模板键的旧战报。
    if actor_key == "bloodthirsty_cavalry" or actor_name == "嗜血铁骑":
        return ""
    phase = attack.get("priority_phase", action.get("priority_phase", round_priority))
    priority = attack.get("priority", action.get("priority"))
    # Bow technology grants priority -2, while sword technology grants -1.
    # Units retain priority in ordinary rounds, which are not technology phases.
    if priority == -2 and phase in (-2, -1):
        return "凤舞九天"
    if priority == -1 and phase == -1:
        return "驭剑之术"
    return ""


def report_event_side(event: object, action: object = None, *, target: bool = False) -> str:
    """Resolve battle sides from immutable event snapshots, including older logs."""
    event = event if isinstance(event, Mapping) else {}
    action = action if isinstance(action, Mapping) else {}
    if target:
        state = event.get("target_state")
        if isinstance(state, Mapping) and state.get("side") in ("attacker", "defender"):
            return str(state["side"])
    for source in (event, action):
        side = source.get("side")
        for state_key in ("actor_state", "unit_state"):
            state = source.get(state_key)
            if side not in ("attacker", "defender") and isinstance(state, Mapping):
                side = state.get("side")
        if side in ("attacker", "defender"):
            if target:
                return "defender" if side == "attacker" else "attacker"
            return str(side)
    return ""


def group_action_recovery_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """将行动前恢复和嗜血狂怒并入本次出手；只处理展示副本，保留原始战斗日志。"""
    rows = list(events)
    attached: dict[int, list[dict[str, Any]]] = {}
    removed: set[int] = set()
    for index, event in enumerate(rows):
        if index in removed:
            continue
        if not (
            (event.get("type") == "heal" and event.get("effect") == "五气朝元")
            or (event.get("type") == "passive" and event.get("effect") in {"绝处逢生", "嗜血狂怒"})
        ):
            continue
        unit = event.get("unit")
        side = report_event_side(event)
        if not unit:
            continue
        for action_index in range(index + 1, len(rows)):
            following = rows[action_index]
            following_side = report_event_side(following)
            if side and following_side and side != following_side:
                break
            if following.get("type") in ("heal", "passive") and following.get("unit") == unit:
                continue
            if (
                following.get("type") in (None, "attack")
                and not following.get("status")
                and following.get("actor") == unit
            ):
                attached.setdefault(action_index, []).extend(dict(row) for row in rows[index:action_index])
                removed.update(range(index, action_index))
            break
    if not removed:
        return rows

    grouped: list[dict[str, Any]] = []
    for index, event in enumerate(rows):
        if index in removed:
            continue
        row = {**event, "order": len(grouped) + 1}
        if index in attached:
            recoveries: list[dict[str, Any]] = []
            for recovery in attached[index]:
                if recoveries and recovery.get("effect") == recoveries[-1].get("effect") == "绝处逢生":
                    previous = recoveries[-1]
                    healed = previous.get("healed", 0) + recovery.get("healed", 0)
                    previous.update(recovery)
                    previous["healed"] = healed
                else:
                    recoveries.append(recovery)
            row["action_recovery_events_before"] = recoveries
        grouped.append(row)
    return grouped


def reflect_effect_name(attack: Mapping[str, Any]) -> str:
    """优先显示结算时记录的反伤来源；旧门客战报使用通用反震名称。"""
    name = str(attack.get("reflect_effect") or "").strip()
    if name:
        return name
    target_state = attack.get("target_state") or {}
    if isinstance(target_state, Mapping) and target_state.get("kind") == "troop":
        return "护身剑罡"
    if (
        attack.get("target_template_key")
        or attack.get("target_guest_id")
        or attack.get("target_is_boss")
        or (isinstance(target_state, Mapping) and target_state.get("kind") == "guest")
    ):
        return "反震"
    return "护身剑罡"
