"""占领集市的轮换货单；读取不落库，购买复用赛季事务与绑定奖励。"""

from __future__ import annotations

import hashlib
import random
import re
from datetime import datetime, timedelta
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from core.exceptions import GameError
from gameplay.models import ItemTemplate, SeasonClaim

from .warfare import campaign_open

_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _integer(value: Any, field: str, minimum: int = 1, maximum: int = 1_000_000) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ImproperlyConfigured(f"赛季集市配置 {field} 须为{minimum}—{maximum}的整数")


def validate_market_control(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        raise ImproperlyConfigured("赛季集市配置须包含布尔 enabled")
    if not value["enabled"]:
        return
    _integer(value.get("refresh_hours"), "refresh_hours", maximum=168)
    _integer(value.get("owner_return_bonus_percent"), "owner_return_bonus_percent", minimum=0, maximum=10)
    pool = value.get("pool")
    if not isinstance(pool, list) or not pool:
        raise ImproperlyConfigured("赛季集市 pool 须为非空货品列表")
    _integer(value.get("offer_count"), "offer_count", maximum=len(pool))
    seen = set()
    for row in pool:
        if not isinstance(row, dict):
            raise ImproperlyConfigured("赛季集市货品须为映射")
        key = row.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 24 or not _KEY.fullmatch(key) or key in seen:
            raise ImproperlyConfigured("赛季集市货品 key 须唯一且不超过24个字母、数字、下划线或连字符")
        seen.add(key)
        if not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ImproperlyConfigured("赛季集市货品须有名称")
        _integer(row.get("cost"), key + ".cost")
        _integer(row.get("limit"), key + ".limit")
        items = row.get("items")
        if not isinstance(items, dict) or not items:
            raise ImproperlyConfigured("赛季集市货品 items 须为非空物品数量映射")
        for item_key, amount in items.items():
            if not isinstance(item_key, str) or not _KEY.fullmatch(item_key):
                raise ImproperlyConfigured("赛季集市货品含无效物品 key")
            _integer(amount, key + ".items." + item_key)


def _config(season: Any) -> dict[str, Any]:
    value = season.rules.get("campaign", {}).get("market_control") if season else None
    validate_market_control(value)
    return value if value and value["enabled"] else {}


def _claim_key(period: int, item_key: str) -> str:
    # 不含阵营，易主或成员阵营变化不能重置个人本轮限购。
    digest = hashlib.sha256(item_key.encode()).hexdigest()[:32]
    return f"market:{period}:{digest}"


def _rotation(season: Any, config: dict[str, Any], now: datetime) -> tuple[int, datetime, list[dict[str, Any]]]:
    interval = config["refresh_hours"] * 3600
    period = max(0, int((now - season.starts_at).total_seconds() // interval))
    refresh_at = season.starts_at + timedelta(seconds=(period + 1) * interval)
    seed = int.from_bytes(hashlib.sha256(f"{season.pk}:{period}".encode()).digest(), "big")
    offers = random.Random(seed).sample(config["pool"], config["offer_count"])
    return period, refresh_at, offers


def market_stock_context(season: Any, member: Any, now: datetime) -> dict[str, Any]:
    config = _config(season)
    if not config:
        return {"enabled": False, "offers": []}
    owner = season.sites.filter(key="river:market").values_list("owner", flat=True).first() or ""
    is_owner = bool(member and member.season_id == season.pk and member.faction and member.faction == owner)
    opened = campaign_open(season, now)
    if not is_owner:
        return {"enabled": True, "owner": owner, "is_owner": False, "open": opened, "offers": []}
    period, refresh_at, selected = _rotation(season, config, now)
    claims = (
        dict(
            member.claims.filter(key__in=[_claim_key(period, row["key"]) for row in selected]).values_list(
                "key", "quantity"
            )
        )
        if member and member.season_id == season.pk
        else {}
    )
    item_keys = {key for row in selected for key in row["items"]}
    from .item_visuals import item_visuals

    item_templates = {
        item.key: item
        for item in ItemTemplate.objects.filter(key__in=item_keys).only("key", "name", "image", "icon", "rarity")
    }
    item_names = {key: item.name for key, item in item_templates.items()}
    loaded = set(item_names)
    offers = []
    for row in selected:
        purchased = claims.get(_claim_key(period, row["key"]), 0)
        remaining = max(0, row["limit"] - purchased)
        ready = set(row["items"]).issubset(loaded)
        affordable = bool(member and member.coins >= row["cost"])
        offers.append(
            {
                **row,
                "purchased": purchased,
                "remaining": remaining,
                "available": bool(is_owner and opened and remaining and affordable and ready),
                "items_loaded": ready,
                "goods": item_visuals(row["items"], item_templates),
                "description": "、".join(
                    f"{item_names.get(key, '待载入物品')} × {amount}" for key, amount in row["items"].items()
                ),
            }
        )
    return {
        "enabled": True,
        "period": period,
        "refresh_at": refresh_at,
        "owner": owner,
        "is_owner": is_owner,
        "open": opened,
        "offers": offers,
    }


def purchase_market_stock_locked(member: Any, manor: Any, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    config = _config(member.season)
    if not config or not campaign_open(member.season, now):
        raise GameError("当前不能购买赛季集市货品")
    if not member.faction or not member.season.sites.filter(key="river:market", owner=member.faction).exists():
        raise GameError("仅占领集市的阵营可以购买本轮货品")
    period, _, selected = _rotation(member.season, config, now)
    requested_period = payload.get("market_period")
    if isinstance(requested_period, bool) or not isinstance(requested_period, int) or requested_period != period:
        raise GameError("集市货单已刷新，请刷新页面后购买")
    offer = next((row for row in selected if row["key"] == payload.get("item_key")), None)
    if offer is None:
        raise GameError("该货品不在本轮集市货单中")
    key = _claim_key(period, offer["key"])
    claim = SeasonClaim.objects.select_for_update().filter(member=member, key=key).first()
    if claim and claim.quantity >= offer["limit"]:
        raise GameError("该货品的个人本轮限购次数已用完")
    if member.coins < offer["cost"]:
        raise GameError("个人军资不足")
    from .actions import _grant

    _grant(manor, offer["items"])
    member.coins -= offer["cost"]
    member.save(update_fields=["coins"])
    if claim:
        claim.quantity += 1
        claim.save(update_fields=["quantity"])
    else:
        SeasonClaim.objects.create(member=member, key=key)
    return {"message": "已购买绑定物品：" + offer["name"], "item_key": offer["key"], "market_period": period}
