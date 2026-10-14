from __future__ import annotations

import logging
import re
from typing import Any

from django.db import IntegrityError, transaction

from core.exceptions import GameError, MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.constants import ManorNameConstants
from gameplay.models import Manor, Message

logger = logging.getLogger(__name__)

MANOR_MESSAGE_BEST_EFFORT_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


class ManorNameConflictError(GameError):
    """Raised when the requested manor name is already occupied."""

    error_code = "MANOR_NAME_CONFLICT"


class ManorRenameValidationError(GameError):
    """Raised when the requested manor name is invalid."""

    error_code = "MANOR_RENAME_VALIDATION_ERROR"


class ManorRenameItemError(GameError):
    """Raised when rename-card lookup or consumption fails."""

    error_code = "MANOR_RENAME_ITEM_ERROR"


MANOR_NAME_MIN_LENGTH = ManorNameConstants.MIN_LENGTH
MANOR_NAME_MAX_LENGTH = ManorNameConstants.MAX_LENGTH
MANOR_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5a-zA-Z0-9_]+$")
BANNED_WORDS = ManorNameConstants.BANNED_WORDS


def _normalize_persisted_manor_id(raw_manor_id: object, *, contract_name: str) -> int:
    if raw_manor_id is None or isinstance(raw_manor_id, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_manor_id!r}")
    raw_for_int: Any = raw_manor_id
    try:
        manor_id = int(raw_for_int)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_manor_id!r}") from exc
    if manor_id <= 0:
        raise AssertionError(f"invalid {contract_name}: {raw_manor_id!r}")
    return manor_id


def _normalize_manor_name_input(raw_name: object, *, contract_name: str) -> str:
    if not isinstance(raw_name, str):
        raise AssertionError(f"invalid {contract_name}: {raw_name!r}")
    return raw_name.strip()


def validate_manor_name(name: str) -> tuple[bool, str]:
    if not name or not name.strip():
        return False, "名称不能为空"

    name = name.strip()
    if len(name) < MANOR_NAME_MIN_LENGTH:
        return False, f"名称至少需要{MANOR_NAME_MIN_LENGTH}个字符"
    if len(name) > MANOR_NAME_MAX_LENGTH:
        return False, f"名称最多{MANOR_NAME_MAX_LENGTH}个字符"
    if not MANOR_NAME_PATTERN.match(name):
        return False, "名称仅支持中文、英文、数字和下划线"

    name_lower = name.lower()
    for word in BANNED_WORDS:
        if word.lower() in name_lower:
            return False, "名称包含敏感词"

    return True, ""


def is_manor_name_available(name: str, exclude_manor_id: int | None = None) -> bool:
    normalized_name = _normalize_manor_name_input(name, contract_name="manor name")
    queryset = Manor.objects.filter(name__iexact=normalized_name)
    if exclude_manor_id:
        normalized_exclude_id = _normalize_persisted_manor_id(
            exclude_manor_id,
            contract_name="exclude manor id",
        )
        queryset = queryset.exclude(id=normalized_exclude_id)
    return not queryset.exists()


@transaction.atomic
def rename_manor(manor: Manor, new_name: str, consume_item: bool = True) -> None:
    from gameplay.models import InventoryItem, ItemTemplate

    manor_id = _normalize_persisted_manor_id(getattr(manor, "pk", None), contract_name="persisted manor")
    if not isinstance(consume_item, bool):
        raise AssertionError(f"invalid manor rename consume_item: {consume_item!r}")
    new_name = _normalize_manor_name_input(new_name, contract_name="manor rename new_name")
    valid, error_msg = validate_manor_name(new_name)
    if not valid:
        raise ManorRenameValidationError(error_msg)
    if not is_manor_name_available(new_name, exclude_manor_id=manor_id):
        raise ManorNameConflictError("该名称已被使用")

    if consume_item:
        try:
            rename_card = ItemTemplate.objects.get(key="manor_rename_card")
        except ItemTemplate.DoesNotExist:
            raise ManorRenameItemError("庄园命名卡道具未配置")

        inventory_item = (
            InventoryItem.objects.select_for_update()
            .filter(
                manor=manor,
                template=rename_card,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                quantity__gt=0,
            )
            .first()
        )

        if not inventory_item:
            raise ManorRenameItemError("您没有庄园命名卡")

        from gameplay.services.inventory.core import consume_inventory_item_locked

        consume_inventory_item_locked(inventory_item, 1)

    old_name = manor.name or manor.display_name
    manor.name = new_name
    try:
        manor.save(update_fields=["name"])
    except IntegrityError:
        logger.warning("Manor rename race condition detected for %s by user %s", new_name, manor.user_id)
        raise ManorNameConflictError("该名称已被使用")

    from gameplay.services.utils.messages import create_message

    def _send_rename_message() -> None:
        try:
            create_message(
                manor=manor,
                kind=Message.Kind.SYSTEM,
                title="庄园更名成功",
                body=f"您的庄园已从「{old_name}」更名为「{new_name}」",
            )
        except MANOR_MESSAGE_BEST_EFFORT_EXCEPTIONS as exc:
            logger.warning(
                "manor rename message failed: manor_id=%s old_name=%s new_name=%s error=%s",
                manor.id,
                old_name,
                new_name,
                exc,
                exc_info=True,
            )

    transaction.on_commit(_send_rename_message)


def get_rename_card_count(manor: Manor) -> int:
    from gameplay.models import InventoryItem, ItemTemplate

    manor_id = _normalize_persisted_manor_id(getattr(manor, "pk", None), contract_name="persisted manor")
    try:
        rename_card = ItemTemplate.objects.get(key="manor_rename_card")
    except ItemTemplate.DoesNotExist:
        return 0

    item = InventoryItem.objects.filter(
        manor_id=manor_id,
        template=rename_card,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).first()

    return item.quantity if item else 0
