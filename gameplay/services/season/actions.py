"""赛季命令：事务内校验、派遣与奖励入账，出征由后台独立结算。"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import ItemTemplate, Manor, Season, SeasonAction, SeasonClaim, SeasonMember
from gameplay.services.equipment_affixes import roll_equipment_reward_items_locked
from gameplay.services.inventory.core import add_item_to_inventory_locked
from guests.services.recruitment_guests import create_guest_from_template
from guests.services.training import ensure_auto_training

from .catalog import available_summon_templates, has_summon_catalog, reward_catalog, shop_catalog
from .rules import FACTION_NAMES, season_state

# 这些行动会改变往季奖励横幅（领取、使用卷轴），提交后立即失效缓存。
_REWARD_CACHE_ACTIONS = frozenset({"claim", "claim_final", "claim_weekly", "summon", "summon_fallback"})


def _identity(token: str, action: str, payload: dict[str, Any]) -> tuple[UUID, str]:
    try:
        parsed = UUID(str(token))
    except (ValueError, TypeError, AttributeError):
        raise GameError("操作凭证无效，请刷新页面重试") from None
    digest = hashlib.sha256(json.dumps([action, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return parsed, digest


def _previous(member: SeasonMember, token: UUID, digest: str) -> dict[str, Any] | None:
    previous = member.actions.filter(token=token).first()
    if previous:
        if previous.request_hash != digest:
            raise GameError("该操作凭证已用于另一项操作，请重新提交")
        return previous.result
    return None


def _lock(
    manor_id: int, season_id: int, action: str = "", payload: dict[str, Any] | None = None
) -> tuple[Season, Manor, SeasonMember | None]:
    try:
        season = Season.objects.select_for_update().get(pk=season_id)
        if action in {"rally_join", "rally_cancel", "recall"}:
            from gameplay.models import SeasonExpedition

            payload = payload or {}
            related = SeasonExpedition.objects.filter(member__season=season, status="gathering")
            if action == "recall":
                rally_id = (
                    related.filter(pk=payload.get("run_id", 0), member__manor_id=manor_id)
                    .values_list("rally_id", flat=True)
                    .first()
                )
            else:
                rally_id = payload.get("rally_id", 0)
            if rally_id:
                manor_ids = set(related.filter(rally_id=rally_id).values_list("member__manor_id", flat=True)) | {
                    manor_id
                }
                list(Manor.objects.select_for_update().filter(pk__in=manor_ids).order_by("pk"))
        manor = Manor.objects.select_for_update().get(pk=manor_id)
    except (Season.DoesNotExist, Manor.DoesNotExist):
        raise GameError("赛季或庄园不存在") from None
    member = SeasonMember.objects.select_for_update().filter(season=season, manor=manor).first()
    return season, manor, member


def _record(
    member: SeasonMember, token: UUID, digest: str, action: str, result: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    date = kwargs.pop("date", timezone.localdate())
    week = kwargs.pop("week", season_state(member.season)["week"])
    SeasonAction.objects.create(
        member=member, token=token, request_hash=digest, kind=action, date=date, week=week, result=result, **kwargs
    )
    return result


def _grant(manor: Manor, items: dict[str, int]) -> None:
    if not items:
        raise GameError("奖励尚未配置，请联系管理员")
    templates = {item.key: item for item in ItemTemplate.objects.filter(key__in=items)}
    if len(templates) != len(items):
        raise GameError("奖励物品尚未载入，未扣除任何赛季币")
    actual, actual_templates = roll_equipment_reward_items_locked(items, templates=templates)
    for key, quantity in actual.items():
        add_item_to_inventory_locked(manor, key, quantity, template=actual_templates.get(key), is_bound=True)


def _claim(member: SeasonMember, manor: Manor, payload: dict[str, Any]) -> dict[str, Any]:
    reward = next(
        (
            row
            for row in reward_catalog(member.faction, rules=member.season.rules)
            if row["key"] == payload.get("reward_key")
        ),
        None,
    )
    if not reward:
        raise GameError("奖励不存在")
    if member.merit < reward["threshold"]:
        raise GameError("个人功勋尚未达到领取要求")
    key = "reward:" + reward["key"]
    if member.claims.filter(key=key).exists():
        raise GameError("该奖励已经领取")
    if reward.get("kind") == "scroll":
        member.scroll_available = True
        member.save(update_fields=["scroll_available"])
    else:
        _grant(manor, reward["items"])
    SeasonClaim.objects.create(member=member, key=key)
    return {"message": "已领取" + reward["name"]}


def _exchange(member: SeasonMember, manor: Manor, payload: dict[str, Any]) -> dict[str, Any]:
    item = next(
        (
            row
            for row in shop_catalog(member.faction, rules=member.season.rules)
            if row["key"] == payload.get("item_key")
        ),
        None,
    )
    if not item:
        raise GameError("兑换物品不存在")
    key = "shop:" + item["key"]
    claim = member.claims.filter(key=key).first()
    if claim and claim.quantity >= item["limit"]:
        raise GameError("本赛季兑换次数已用完")
    if member.coins < item["cost"]:
        raise GameError("个人赛季币不足")
    _grant(manor, item["items"])
    member.coins -= item["cost"]
    member.save(update_fields=["coins"])
    if claim:
        claim.quantity += 1
        claim.save(update_fields=["quantity"])
    else:
        SeasonClaim.objects.create(member=member, key=key)
    return {"message": "已兑换" + item["name"]}


def _summon(member: SeasonMember, manor: Manor, payload: dict[str, Any], fallback: bool) -> dict[str, Any]:
    if not member.scroll_available or member.scroll_used:
        raise GameError("没有可用的赛季结缘卷轴")
    if not has_summon_catalog(member.faction, rules=member.season.rules):
        raise GameError("本阵营人物目录尚未载入，卷轴已保留，请联系管理员")
    available = available_summon_templates(manor, member.faction, rules=member.season.rules)
    if fallback:
        if available:
            raise GameError("本阵营仍有未获得的可招募人物，请选择召唤候选")
        _grant(manor, member.season.rules["summon_fallback_items"])
        result: dict[str, Any] = {"message": "本阵营人物已集齐，已领取结缘替代材料"}
    else:
        ids = payload.get("template_ids", [])
        pick_count = min(member.season.rules.get("summon_pick_count", 3), len(available))
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or len(ids) != pick_count or not available:
            raise GameError(f"请选择 {pick_count} 名不同的有效候选人物")
        by_id = {template.id: template for template in available}
        if any(template_id not in by_id for template_id in ids):
            raise GameError("候选人物已获得或不再符合招募条件，请重新选择")
        if manor.active_guest_count >= manor.guest_capacity:
            raise GameError("门客容量不足，卷轴已保留，请先腾出位置")
        template = by_id[secrets.choice(ids)]
        guest = create_guest_from_template(manor=manor, template=template, assign_fallback_avatar=True)
        ensure_auto_training(guest)
        result = {"message": f"结缘成功，获得门客 {guest.display_name}", "guest_id": guest.pk}
    member.scroll_available = False
    member.scroll_used = True
    member.save(update_fields=["scroll_available", "scroll_used"])
    return result


def perform_action(manor: Manor, season_id: int, action: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .campaign_rewards import claim_final_locked
    from .economy import upgrade_locked
    from .expeditions import dispatch_locked, recall_locked
    from .rallies import cancel_rally_locked, create_rally_locked, join_rally_locked
    from .registration import register_locked
    from .warfare import campaign_nodes, campaign_open

    parsed, digest = _identity(token, action, payload)
    with transaction.atomic():
        season, locked_manor, member = _lock(manor.id, season_id, action, payload)
        if member:
            previous = _previous(member, parsed, digest)
            if previous is not None:
                return previous
        now = timezone.now()
        if action == "join":
            from .playability_rules import playability_rules

            if playability_rules(season)["enabled"]:
                member = register_locked(
                    season,
                    locked_manor,
                    payload.get("preference", "random"),
                    now,
                    registration_playtime=payload.get("registration_playtime", "any"),
                )
            else:
                member = register_locked(season, locked_manor, payload.get("preference", "random"), now)
            label = FACTION_NAMES.get(member.faction, "等待声望平衡分组")
            return _record(
                member,
                parsed,
                digest,
                action,
                {"message": f"报名成功：{label}，每次活动可自由选择门客"},
                date=timezone.localdate(now),
            )
        if not member:
            raise GameError("请先报名参加本赛季")
        if member.faction not in FACTION_NAMES:
            from .development_rules import feature_rules

            allowed_pending = feature_rules(season, "activities")["enabled"] and (
                action == "recall"
                or (action == "dispatch" and payload.get("mode") in {"pve", "explore", "npc_intercept"})
            )
            if not allowed_pending:
                raise GameError("报名已记录；分组前可探索或讨伐，其他玩法等待阵营分配")
        if action == "reservation_create":
            from .reservations import create_reservation_locked

            result = create_reservation_locked(member, payload, now)
        elif action == "reservation_cancel":
            from .reservations import cancel_reservation_locked

            result = cancel_reservation_locked(member, now)
        elif action == "claim_weekly":
            from .weekly_campaign import claim_weekly_locked

            result = claim_weekly_locked(member, locked_manor, payload.get("week_number", 0))
        elif action == "rally_create":
            result = create_rally_locked(member, locked_manor, payload, now)
        elif action == "rally_join":
            result = join_rally_locked(member, locked_manor, payload, now)
        elif action == "rally_cancel":
            result = cancel_rally_locked(member, payload.get("rally_id", 0), now)
        elif action == "dispatch":
            result = dispatch_locked(member, locked_manor, payload, now)
        elif action == "recall":
            result = recall_locked(member, payload.get("run_id", 0), now)
        elif action == "market_exchange":
            from .market import exchange_resources_locked

            result = exchange_resources_locked(member, payload, now)
        elif action == "market_purchase":
            from .market_stock import purchase_market_stock_locked

            result = purchase_market_stock_locked(member, locked_manor, payload, now)
        elif action == "upgrade":
            if not campaign_open(season, now):
                raise GameError("当前不在赛季建设期")
            result = upgrade_locked(member, payload.get("upgrade_key", ""), now)
        elif action == "claim_final":
            result = claim_final_locked(member, locked_manor)
        elif action == "claim":
            result = _claim(member, locked_manor, payload)
        elif action == "exchange":
            unlock_day = campaign_nodes(season)["river:market"]["unlock_day"]
            if season_state(season, now)["day"] < unlock_day:
                raise GameError(f"沿岸集市于第 {unlock_day} 天开放")
            result = _exchange(member, locked_manor, payload)
        elif action in {"summon", "summon_fallback"}:
            result = _summon(member, locked_manor, payload, action == "summon_fallback")
        else:
            raise GameError("赛季已改用真实派遣，请刷新页面选择行动")
        if action in _REWARD_CACHE_ACTIONS:
            from gameplay.selectors.season_archive import invalidate_previous_season_reward

            manor_id = manor.pk
            transaction.on_commit(lambda: invalidate_previous_season_reward(manor_id))
        return _record(member, parsed, digest, action, result, date=timezone.localdate(now))
