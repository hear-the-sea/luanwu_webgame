from __future__ import annotations

from django import template

from battle.report_events import reflect_effect_name, report_event_side, secondary_attack_kind, troop_phase_skill

register = template.Library()
register.simple_tag(troop_phase_skill)
register.simple_tag(report_event_side)
register.simple_tag(reflect_effect_name)
register.simple_tag(secondary_attack_kind)


@register.filter
def is_boss_passive_reflect(effect: object) -> bool:
    """统一首领被动反震显示，同时兼容旧战报中的技能来源。"""
    return effect in ("乾坤大挪移", "明教号令", "圣火狂势")


@register.simple_tag
def battle_side_label(side: object, visible_side: object = "attacker", spectator=False, opposite=False) -> str:
    """按观看者视角显示阵营；反击和受击阵营可通过 opposite 换算。"""
    if side not in ("attacker", "defender"):
        return ""
    if opposite:
        side = "defender" if side == "attacker" else "attacker"
    if spectator:
        return "进攻方" if side == "attacker" else "防守方"
    return "我方" if side == visible_side else "敌方"
