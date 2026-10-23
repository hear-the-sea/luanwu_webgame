from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from copy import deepcopy
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
    from battle.troops import load_troop_templates_from_yaml
    from core.game_data.technology import get_troop_class_for_key

    actor_key = attack.get("actor_template_key", action.get("actor_template_key"))
    actor_name = attack.get("actor", action.get("actor"))
    # 旧战报没有模板键，按配置中的名称恢复兵种；不因自带先攻误判科技。
    if not actor_key:
        actor_key = next(
            (key for key, template in load_troop_templates_from_yaml().items() if template["label"] == actor_name),
            "",
        )
    troop_class = get_troop_class_for_key(actor_key)
    phase = attack.get("priority_phase", action.get("priority_phase", round_priority))
    priority = attack.get("priority", action.get("priority"))
    # 弓系远射和普通先攻统一显示凤舞九天，普通回合不显示；仅调整战报命名。
    if troop_class == "gong" and priority in (-2, -1) and phase in (-2, -1):
        return "凤舞九天"
    if troop_class == "jian" and priority == -1 and phase == -1:
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


def _merge_bloodthirsty_recovery(row: dict[str, Any]) -> dict[str, Any]:
    """将同次出手的扣血与追加目标说明合并，只修改展示副本。"""
    recoveries = row.get("action_recovery_events_before") or []
    if not any(recovery.get("effect") == "嗜血狂怒" and recovery.get("lost") for recovery in recoveries):
        return row
    row = deepcopy(row)
    recoveries = row["action_recovery_events_before"]
    for recovery in list(recoveries):
        if recovery.get("effect") != "嗜血狂怒" or not recovery.get("lost"):
            continue
        groups = [(recoveries, row)] + [
            (attack.get("passive_events_before") or [], attack) for attack in _iter_nested_damage_events([row])
        ]
        for passives, attack in groups:
            match = next(
                (
                    passive
                    for passive in passives
                    if (
                        passive.get("effect") == "嗜血狂怒"
                        and passive.get("unit") == recovery.get("unit")
                        and passive is not recovery
                        and passive.get("message") in {"随机追加目标", "当前目标追加伤害", "本次攻击追加真实伤害"}
                        and (report_event_side(passive, attack) or report_event_side(row))
                        == report_event_side(recovery, row)
                    )
                ),
                None,
            )
            if match is not None:
                lost = recovery["lost"]
                recovery.update(match)
                recovery["lost"] = lost
                if passives is not recoveries and isinstance(attack, dict):
                    attack["passive_damage_source"] = "嗜血狂怒"
                passives.remove(match)
                break
    return row


def secondary_attack_kind(attack: object, parent: object = None, round_priority: object = None) -> str:
    """按所属攻击段识别技能波及与被动追加目标，避免将普通攻击写成波及。"""
    if not isinstance(attack, Mapping):
        return "normal"
    parent = parent if isinstance(parent, Mapping) else {}
    if attack.get("current_target_bonus") or attack.get("passive_damage_source") == "嗜血狂怒":
        return "passive"
    if any(passive.get("effect") == "嗜血狂怒" for passive in (attack.get("passive_events_before") or [])):
        return "passive"
    phase = attack.get("attack_phase", parent.get("attack_phase"))
    if phase == "normal":
        return "normal"
    if (
        phase == "burst"
        or attack.get("skills")
        or parent.get("skills")
        or troop_phase_skill(attack, parent, round_priority)
    ):
        return "skill"
    return "normal"


def prepare_passive_display_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """将受击被动留在对应攻击下方，区分生效提示与实际反震结算。"""
    result: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        row = dict(event)
        if row.get("type") == "passive" and row.get("effect") == "乾坤大挪移":
            state = row.get("unit_state") or {}
            if state.get("current") == 0 or state.get("status") == "empty":
                continue
            legacy_hit_notice = row.get("message") == "卸力反震"
            if legacy_hit_notice:
                row["message"] = "卸力蓄势，后续受击时反震"
            previous = result[-1] if result else None
            if (
                legacy_hit_notice
                and previous
                and previous.get("target") == row.get("unit")
                and previous.get("type") in (None, "attack")
                and report_event_side(previous, target=True) == report_event_side(row)
            ):
                previous["passive_events_after"] = [*previous.get("passive_events_after", []), row]
                continue
        for key in ("additional_targets", "additional_attacks", "passive_events_before", "passive_events_after"):
            if isinstance(row.get(key), list):
                row[key] = prepare_passive_display_events(row[key])
        result.append(row)
    return result


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
        grouped.append(_merge_bloodthirsty_recovery(row))
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
