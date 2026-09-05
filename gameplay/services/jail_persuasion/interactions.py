from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import ItemInsufficientError, ItemNotFoundError, JailError
from gameplay.constants import BuildingKeys
from gameplay.models import JailInteractionLog, JailPrisoner, Manor, ResourceEvent
from gameplay.services.inventory.core import get_item_quantity
from gameplay.services.jail_expiration import release_expired_prisoner_if_needed
from gameplay.services.resources import spend_resources_locked
from guests.models import Guest, GuestStatus
from trade.services.auction.gold_bars import consume_available_gold_bars_locked

from .effects import EffectResult, normalize_speaker_ratio, resolve_effect
from .milestones import pending_milestone_stage
from .profiles import (
    METHOD_MIGHT,
    METHOD_ORDER,
    METHOD_REASON,
    calculate_affinities,
    choose_stance,
    choose_taboo,
    clamp,
    get_clue_keys,
    load_jail_persuasion_profiles,
    rarity_difficulty,
    render_copy,
    stable_seed,
)

GOLD_BAR_ITEM_KEY = "gold_bar"
SPEAKER_METHODS = {METHOD_REASON, METHOD_MIGHT}


@dataclass(frozen=True)
class ObservationResult:
    prisoner: JailPrisoner
    scores: dict[str, int]
    clue_keys: list[str]


@dataclass(frozen=True)
class InteractionResult:
    prisoner: JailPrisoner
    log: JailInteractionLog
    outcome: str
    heart_delta: int
    affinity_delta: int
    speaker_loyalty_delta: int
    speaker_loyalty: int | None
    copy_params: dict[str, object]
    copy_text: str
    pending_milestone_stage: int


def roll_variations() -> tuple[int, int]:
    return random.randint(-1, 1), random.randint(-2, 2)


def daily_action_limit(manor: Manor) -> int:
    level = clamp(manor.get_building_level(BuildingKeys.JAIL), 1, 5)
    return int(load_jail_persuasion_profiles()["daily_actions_by_jail_level"][level])


def _get_locked_prisoner(manor: Manor, prisoner_id: int) -> JailPrisoner:
    prisoner = (
        JailPrisoner.objects.select_for_update()
        .select_related("guest_template")
        .filter(pk=prisoner_id, captor=manor, status=JailPrisoner.Status.HELD)
        .first()
    )
    if prisoner is None:
        raise JailError("囚徒不存在或已处理")
    return prisoner


def _initialize_persuasion_state(prisoner: JailPrisoner, *, now: Any) -> dict[str, int]:
    scores = calculate_affinities(
        prisoner.guest_template,
        captured_loyalty=int(prisoner.captured_loyalty),
        original_level=int(prisoner.original_level),
    )
    prisoner.stance_method = choose_stance(
        prisoner_id=int(prisoner.id),
        template_key=str(prisoner.guest_template.key),
        captured_at=prisoner.captured_at,
        scores=scores,
    )
    prisoner.taboo_method = choose_taboo(scores)
    prisoner.revealed_level = max(1, int(prisoner.revealed_level or 0))
    prisoner.observed_at = now
    return scores


def observe_prisoner(manor: Manor, prisoner_id: int) -> ObservationResult:
    result = _observe_prisoner(manor, prisoner_id)
    if result is None:
        raise JailError("囚徒已关押满30天，已自动释放")
    return result


@transaction.atomic
def _observe_prisoner(manor: Manor, prisoner_id: int) -> ObservationResult | None:
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    prisoner = _get_locked_prisoner(locked_manor, prisoner_id)
    if release_expired_prisoner_if_needed(prisoner):
        return None
    if prisoner.observed_at is not None:
        raise JailError("已经察言观色，无需重复观察")

    scores = _initialize_persuasion_state(prisoner, now=timezone.now())
    prisoner.save(update_fields=["stance_method", "taboo_method", "revealed_level", "observed_at"])
    clue_keys = get_clue_keys(
        stance_method=prisoner.stance_method,
        revealed_level=prisoner.revealed_level,
        prisoner_id=int(prisoner.id),
        template_key=str(prisoner.guest_template.key),
        captured_at=prisoner.captured_at,
    )
    return ObservationResult(prisoner=prisoner, scores=scores, clue_keys=clue_keys)


def _reset_daily_counter(prisoner: JailPrisoner, usage_date: date) -> None:
    if prisoner.interaction_date != usage_date:
        prisoner.interaction_date = usage_date
        prisoner.interactions_today = 0


def _lock_speaker(manor: Manor, speaker_id: int | None, *, usage_date: date) -> Guest:
    if speaker_id is None:
        raise JailError("请选择一名说客")
    speaker = (
        Guest.objects.select_for_update()
        .select_related("template")
        .filter(pk=speaker_id, manor=manor, status=GuestStatus.IDLE)
        .first()
    )
    if speaker is None:
        raise JailError("说客不存在、不属于当前庄园或并非空闲")
    if JailInteractionLog.objects.filter(speaker=speaker, usage_date=usage_date).exists():
        raise JailError(f"{speaker.display_name} 今日已经担任过说客")
    return speaker


def _speaker_values(method: str, speaker: Guest, prisoner: JailPrisoner) -> tuple[int, int, float]:
    if method == METHOD_REASON:
        speaker_value = int(speaker.template.base_intellect)
        prisoner_value = max(1, int(prisoner.guest_template.base_intellect))
    else:
        speaker_value = int(speaker.template.base_attack)
        prisoner_value = max(1, int(prisoner.guest_template.base_attack))
    ratio = normalize_speaker_ratio(speaker_value / prisoner_value)
    return speaker_value, prisoner_value, ratio


def _consume_method_cost(manor: Manor, method: str) -> dict[str, int]:
    configured_cost = dict(load_jail_persuasion_profiles()["methods"][method]["cost"])
    resource_cost = {
        key: int(value) for key, value in configured_cost.items() if key in {"silver", "grain"} and int(value) > 0
    }
    if resource_cost:
        spend_resources_locked(
            manor,
            resource_cost,
            note="监牢礼贤下士",
            reason=ResourceEvent.Reason.RECRUIT_COST,
        )
    gold_cost = int(configured_cost.get(GOLD_BAR_ITEM_KEY, 0) or 0)
    if gold_cost > 0:
        try:
            consume_available_gold_bars_locked(manor, gold_cost)
        except (ItemInsufficientError, ItemNotFoundError) as exc:
            available = exc.context.get("available", get_item_quantity(manor, GOLD_BAR_ITEM_KEY))
            raise JailError(f"金条不足，需要 {gold_cost} 根（当前 {available} 根）") from exc
    return {key: int(value) for key, value in configured_cost.items() if int(value) > 0}


def _feedback_copy(method: str, outcome: str, *, prisoner: JailPrisoner, usage_date: date) -> dict[str, str]:
    copies = load_jail_persuasion_profiles()["feedback"][method][outcome]
    index = stable_seed(
        prisoner.id,
        prisoner.guest_template.key,
        usage_date.isoformat(),
        prisoner.interactions_today,
        method,
        outcome,
    ) % len(copies)
    return copies[index]


def _create_log_with_speaker_guard(**values: Any) -> JailInteractionLog:
    try:
        with transaction.atomic():
            return JailInteractionLog.objects.create(**values)
    except IntegrityError as exc:
        speaker = values.get("speaker")
        usage_date = values.get("usage_date")
        speaker_conflict = (
            speaker is not None
            and usage_date is not None
            and JailInteractionLog.objects.filter(speaker=speaker, usage_date=usage_date).exists()
        )
        if speaker_conflict:
            raise JailError("该说客今日已经担任过说客") from exc
        raise


def interact_prisoner(
    manor: Manor,
    prisoner_id: int,
    *,
    method: str,
    speaker_id: int | None = None,
    lazy_observe: bool = False,
) -> InteractionResult:
    result = _interact_prisoner(
        manor,
        prisoner_id,
        method=method,
        speaker_id=speaker_id,
        lazy_observe=lazy_observe,
    )
    if result is None:
        raise JailError("囚徒已关押满30天，已自动释放")
    return result


@transaction.atomic
def _interact_prisoner(
    manor: Manor,
    prisoner_id: int,
    *,
    method: str,
    speaker_id: int | None = None,
    lazy_observe: bool = False,
) -> InteractionResult | None:
    normalized_method = str(method or "").strip()
    if normalized_method not in METHOD_ORDER:
        raise JailError("未知的招降手段")

    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    prisoner = _get_locked_prisoner(locked_manor, prisoner_id)
    if release_expired_prisoner_if_needed(prisoner):
        return None
    if prisoner.observed_at is None:
        if not lazy_observe:
            raise JailError("请先察言观色，再选择招降手段")
        _initialize_persuasion_state(prisoner, now=timezone.now())

    if pending_milestone_stage(prisoner):
        raise JailError("请先处理当前归心事件")

    usage_date = timezone.localdate()
    _reset_daily_counter(prisoner, usage_date)
    if int(prisoner.interactions_today) >= daily_action_limit(locked_manor):
        raise JailError("今日招降次数已用完")
    if (
        normalized_method in SPEAKER_METHODS
        and JailInteractionLog.objects.filter(
            prisoner=prisoner,
            usage_date=usage_date,
            method=normalized_method,
        ).exists()
    ):
        label = load_jail_persuasion_profiles()["methods"][normalized_method]["label"]
        raise JailError(f"今日已经使用过{label}")
    if normalized_method not in SPEAKER_METHODS and speaker_id is not None:
        raise JailError("该招降手段不需要说客")

    speaker: Guest | None = None
    speaker_base_value: int | None = None
    speaker_ratio: float | None = None
    speaker_archetype = ""
    if normalized_method in SPEAKER_METHODS:
        speaker = _lock_speaker(locked_manor, speaker_id, usage_date=usage_date)
        speaker_base_value, _prisoner_base_value, speaker_ratio = _speaker_values(
            normalized_method,
            speaker,
            prisoner,
        )
        speaker_archetype = str(speaker.template.archetype or "")

    resource_cost = _consume_method_cost(locked_manor, normalized_method)
    same_method_streak = int(prisoner.same_method_streak or 0) + 1 if prisoner.last_method == normalized_method else 1
    scores = calculate_affinities(
        prisoner.guest_template,
        captured_loyalty=int(prisoner.captured_loyalty),
        original_level=int(prisoner.original_level),
    )
    heart_variation, affinity_variation = roll_variations()
    effect: EffectResult = resolve_effect(
        method=normalized_method,
        base_score=scores[normalized_method],
        stance_method=prisoner.stance_method,
        taboo_method=prisoner.taboo_method,
        rarity_difficulty_value=rarity_difficulty(prisoner.guest_template),
        original_level=int(prisoner.original_level),
        same_method_streak=same_method_streak,
        speaker_ratio=speaker_ratio,
        speaker_archetype=speaker_archetype,
        heart_variation=(
            heart_variation if normalized_method not in SPEAKER_METHODS or (speaker_ratio or 0) >= 0.85 else 0
        ),
        affinity_variation=(
            affinity_variation if normalized_method not in SPEAKER_METHODS or (speaker_ratio or 0) >= 0.85 else 0
        ),
    )

    heart_before = clamp(int(prisoner.loyalty), 0, 100)
    affinity_before = clamp(int(prisoner.affinity), 0, 100)
    heart_after = clamp(heart_before + effect.heart_delta, 0, 100)
    affinity_after = clamp(affinity_before + effect.affinity_delta, 0, 100)
    speaker_loyalty_before = int(speaker.loyalty) if speaker is not None else None
    speaker_loyalty_after = speaker_loyalty_before
    if speaker is not None and effect.speaker_loyalty_delta:
        speaker_loyalty_after = clamp(int(speaker.loyalty) + effect.speaker_loyalty_delta, 0, 100)
        speaker.loyalty = speaker_loyalty_after
        speaker.save(update_fields=["loyalty"])

    prisoner.loyalty = heart_after
    prisoner.affinity = affinity_after
    prisoner.interaction_date = usage_date
    prisoner.interactions_today = int(prisoner.interactions_today) + 1
    prisoner.last_method = normalized_method
    prisoner.same_method_streak = same_method_streak
    prisoner.revealed_level = max(2, int(prisoner.revealed_level or 0))
    prisoner.save(
        update_fields=[
            "loyalty",
            "affinity",
            "interaction_date",
            "interactions_today",
            "last_method",
            "same_method_streak",
            "revealed_level",
            "stance_method",
            "taboo_method",
            "observed_at",
        ]
    )

    copy_entry = _feedback_copy(normalized_method, effect.outcome, prisoner=prisoner, usage_date=usage_date)
    copy_params: dict[str, object] = {
        "prisoner_name": prisoner.display_name,
        "speaker_name": speaker.display_name if speaker is not None else "",
        "heart_delta": abs(heart_after - heart_before),
        "affinity_delta": abs(affinity_after - affinity_before),
        "speaker_loyalty_delta": abs((speaker_loyalty_after or 0) - (speaker_loyalty_before or 0)),
    }
    log = _create_log_with_speaker_guard(
        prisoner=prisoner,
        captor=locked_manor,
        method=normalized_method,
        speaker=speaker,
        speaker_name_snapshot=speaker.display_name if speaker is not None else "",
        speaker_template_key_snapshot=speaker.template.key if speaker is not None else "",
        speaker_base_value_snapshot=speaker_base_value,
        speaker_loyalty_before=speaker_loyalty_before,
        speaker_loyalty_after=speaker_loyalty_after,
        usage_date=usage_date,
        heart_before=heart_before,
        heart_after=heart_after,
        affinity_before=affinity_before,
        affinity_after=affinity_after,
        outcome=effect.outcome,
        copy_key=copy_entry["key"],
        copy_params=copy_params,
        resource_cost=resource_cost,
    )
    return InteractionResult(
        prisoner=prisoner,
        log=log,
        outcome=effect.outcome,
        heart_delta=heart_after - heart_before,
        affinity_delta=affinity_after - affinity_before,
        speaker_loyalty_delta=(speaker_loyalty_after or 0) - (speaker_loyalty_before or 0),
        speaker_loyalty=speaker_loyalty_after,
        copy_params=copy_params,
        copy_text=render_copy(copy_entry["key"], copy_params),
        pending_milestone_stage=pending_milestone_stage(prisoner),
    )
