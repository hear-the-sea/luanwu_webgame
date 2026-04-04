from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import GuildMembershipError, GuildValidationError
from gameplay.models import PlayerTroop

from ..models import Guild, GuildMember, GuildTroopDonationLog, GuildTroopStorage


def _lock_active_member(member: GuildMember) -> GuildMember:
    locked_member = (
        GuildMember.objects.select_for_update()
        .select_related("guild", "user__manor")
        .filter(pk=member.pk, is_active=True)
        .first()
    )
    if not locked_member:
        raise GuildMembershipError("您不在帮会中")
    return locked_member


def _normalize_positive_int_mapping(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, int] = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if value is None or isinstance(value, bool):
            continue
        try:
            normalized_value = int(value)
        except (TypeError, ValueError):
            continue
        if normalized_value > 0:
            normalized[normalized_key] = normalized_value
    return normalized


def _get_or_create_locked_storage(*, guild: Guild, troop_template: TroopTemplate) -> GuildTroopStorage:
    storage = GuildTroopStorage.objects.select_for_update().filter(guild=guild, troop_template=troop_template).first()
    if storage is not None:
        return storage

    try:
        with transaction.atomic():
            return GuildTroopStorage.objects.create(
                guild=guild,
                troop_template=troop_template,
                count=0,
            )
    except IntegrityError:
        return GuildTroopStorage.objects.select_for_update().get(
            guild=guild,
            troop_template=troop_template,
        )


def _extract_troops_lost(*, loadout: dict[str, int], report: object | None, side: str) -> dict[str, int]:
    if not report:
        return {}

    losses = getattr(report, "losses", None) or {}
    side_losses = losses.get(side, {}) or {}
    casualties = side_losses.get("casualties", []) or []
    if not isinstance(casualties, list):
        return {}

    from battle.troops import load_troop_templates

    troop_definitions = load_troop_templates()
    troops_lost: dict[str, int] = {}
    for entry in casualties:
        if not isinstance(entry, dict):
            continue
        troop_key = str(entry.get("key") or "").strip()
        if troop_key not in loadout or troop_key not in troop_definitions:
            continue
        try:
            lost = int(entry.get("lost", 0) or 0)
        except (TypeError, ValueError):
            continue
        if lost > 0:
            troops_lost[troop_key] = troops_lost.get(troop_key, 0) + lost
    return troops_lost


def load_guild_troop_loadout(*, guild: Guild) -> dict[str, int]:
    return {
        storage.troop_template.key: int(storage.count or 0)
        for storage in GuildTroopStorage.objects.select_related("troop_template")
        .filter(guild=guild, count__gt=0)
        .order_by("troop_template__priority", "troop_template__id")
    }


def build_guild_defender_setup(*, guild: Guild) -> dict[str, dict[str, int]]:
    troop_loadout = load_guild_troop_loadout(guild=guild)
    if not troop_loadout:
        return {}
    return {"troop_loadout": troop_loadout}


def calculate_surviving_guild_troops(loadout: dict[str, int], report: object | None = None) -> dict[str, int]:
    normalized_loadout = _normalize_positive_int_mapping(loadout)
    if not normalized_loadout:
        return {}
    if not report:
        return normalized_loadout

    troops_lost = _extract_troops_lost(loadout=normalized_loadout, report=report, side="attacker")
    surviving: dict[str, int] = {}
    for troop_key, original_count in normalized_loadout.items():
        lost = min(original_count, troops_lost.get(troop_key, 0))
        remaining = original_count - lost
        if remaining > 0:
            surviving[troop_key] = remaining
    return surviving


@transaction.atomic
def apply_guild_troop_losses(
    *, guild: Guild, loadout: dict[str, int], report: object | None, side: str
) -> dict[str, int]:
    normalized_loadout = _normalize_positive_int_mapping(loadout)
    if not normalized_loadout or not report:
        return {}

    troops_lost = _extract_troops_lost(loadout=normalized_loadout, report=report, side=side)
    if not troops_lost:
        return {}

    storages = {
        storage.troop_template.key: storage
        for storage in GuildTroopStorage.objects.select_for_update()
        .select_related("troop_template")
        .filter(guild=guild, troop_template__key__in=troops_lost.keys())
        .order_by("troop_template__priority", "troop_template__id")
    }

    applied_losses: dict[str, int] = {}
    now = timezone.now()
    for troop_key, lost in troops_lost.items():
        storage = storages.get(troop_key)
        if storage is None:
            continue
        applied_lost = min(int(storage.count or 0), lost)
        if applied_lost <= 0:
            continue
        updated_rows = GuildTroopStorage.objects.filter(pk=storage.pk, count__gte=applied_lost).update(
            count=F("count") - applied_lost,
            updated_at=now,
        )
        if updated_rows != 1:
            continue
        applied_losses[troop_key] = applied_lost

    return applied_losses


@transaction.atomic
def deduct_guild_troops(*, guild: Guild, loadout: dict[str, int]) -> dict[str, int]:
    normalized_loadout = _normalize_positive_int_mapping(loadout)
    if not normalized_loadout:
        return {}

    storages = {
        storage.troop_template.key: storage
        for storage in GuildTroopStorage.objects.select_for_update()
        .select_related("troop_template")
        .filter(guild=guild, troop_template__key__in=normalized_loadout.keys())
        .order_by("troop_template_id")
    }

    for troop_key, quantity in normalized_loadout.items():
        storage = storages.get(troop_key)
        if storage is None or storage.count < quantity:
            raise GuildValidationError(f"帮会护院 {troop_key} 数量不足")

    now = timezone.now()
    for storage in storages.values():
        quantity = normalized_loadout.get(storage.troop_template.key, 0)
        if not quantity:
            continue
        updated_rows = GuildTroopStorage.objects.filter(pk=storage.pk, count__gte=quantity).update(
            count=F("count") - quantity,
            updated_at=now,
        )
        if updated_rows != 1:
            raise GuildValidationError(f"帮会护院 {storage.troop_template.key} 数量不足")

    return normalized_loadout


@transaction.atomic
def add_guild_troops(*, guild: Guild, loadout: dict[str, int]) -> dict[str, int]:
    normalized_loadout = _normalize_positive_int_mapping(loadout)
    if not normalized_loadout:
        return {}

    templates = {
        template.key: template
        for template in TroopTemplate.objects.filter(key__in=normalized_loadout.keys()).order_by("id")
    }
    if len(templates) != len(normalized_loadout):
        invalid_keys = [key for key in normalized_loadout if key not in templates]
        raise GuildValidationError(f"护院参数错误: {', '.join(invalid_keys)}")

    now = timezone.now()
    for troop_key in sorted(normalized_loadout):
        storage = _get_or_create_locked_storage(guild=guild, troop_template=templates[troop_key])
        GuildTroopStorage.objects.filter(pk=storage.pk).update(
            count=F("count") + normalized_loadout[troop_key],
            updated_at=now,
        )

    return normalized_loadout


@transaction.atomic
def donate_troops(*, member: GuildMember, troop_key: str, quantity: int) -> None:
    """
    捐赠玩家护院到帮会公共护院池。

    事务安全：
    - 锁定 GuildMember 与 PlayerTroop 行，防止并发穿透与 TOCTOU
    - 使用 F 表达式进行库存增减，保证原子更新
    """

    if quantity <= 0:
        raise GuildValidationError("捐赠数量必须大于 0")

    normalized_key = str(troop_key or "").strip()
    if not normalized_key:
        raise GuildValidationError("护院参数错误")

    locked_member = _lock_active_member(member)

    player_troop = (
        PlayerTroop.objects.select_for_update()
        .select_related("troop_template")
        .filter(manor=locked_member.user.manor, troop_template__key=normalized_key)
        .first()
    )
    if not player_troop or player_troop.count < quantity:
        raise GuildValidationError(f"护院 {normalized_key} 数量不足")

    updated_rows = PlayerTroop.objects.filter(pk=player_troop.pk, count__gte=quantity).update(
        count=F("count") - quantity,
        updated_at=timezone.now(),
    )
    if updated_rows != 1:
        # 理论上在行锁下不会发生；保底防并发/数据异常导致的负数。
        raise GuildValidationError(f"护院 {normalized_key} 数量不足")

    storage = _get_or_create_locked_storage(guild=locked_member.guild, troop_template=player_troop.troop_template)

    GuildTroopStorage.objects.filter(pk=storage.pk).update(count=F("count") + quantity, updated_at=timezone.now())

    GuildTroopDonationLog.objects.create(
        guild=locked_member.guild,
        member=locked_member,
        troop_template=player_troop.troop_template,
        quantity=quantity,
    )
