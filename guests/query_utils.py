from __future__ import annotations

from django.db.models import BigIntegerField, Case, F, IntegerField, Value, When
from django.db.models.expressions import CombinedExpression
from django.db.models.functions import Cast, Greatest

from core.config import GUEST

from .rarity import GUEST_RARITY_ORDER


def guest_max_hp_expression() -> CombinedExpression:
    """与 Guest.max_hp 一致，供自动回血和用药筛选共用。"""
    base_hp = Greatest(
        Value(GUEST.MIN_HP_FLOOR),
        Cast(F("template__base_hp"), BigIntegerField())
        + F("hp_bonus")
        + Cast(F("defense_stat"), BigIntegerField()) * GUEST.DEFENSE_TO_HP_MULTIPLIER,
        output_field=BigIntegerField(),
    )
    level_hp = (
        Greatest(
            Cast(F("level"), BigIntegerField()) - Value(1),
            Value(0),
            output_field=BigIntegerField(),
        )
        * GUEST.LEVEL_HP_GAIN
    )
    return base_hp + level_hp


def guest_template_rarity_rank_case(field_name: str = "template__rarity") -> Case:
    """
    返回可用于 annotate 的稀有度排序表达式（值越大表示稀有度越高）。
    """
    whens = [When(**{field_name: rarity}, then=Value(rank)) for rank, rarity in enumerate(GUEST_RARITY_ORDER)]
    return Case(
        *whens,
        default=Value(-1),
        output_field=IntegerField(),
    )
