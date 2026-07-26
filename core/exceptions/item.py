"""
物品和装备相关异常
"""

from __future__ import annotations

from .base import GameError

# ============ 物品相关异常 ============


class ItemError(GameError):
    """物品相关错误基类"""

    error_code = "ITEM_ERROR"


class ItemNotFoundError(ItemError):
    """物品不存在"""

    error_code = "ITEM_NOT_FOUND"
    default_message = "物品不存在"


class ItemInsufficientError(ItemError):
    """物品数量不足"""

    error_code = "ITEM_INSUFFICIENT"

    def __init__(
        self,
        item_name: str,
        required: int,
        available: int,
        message: str | None = None,
    ):
        if message is None:
            message = f"{item_name} 数量不足，需要 {required}，当前 {available}"
        super().__init__(
            message,
            item_name=item_name,
            required=required,
            available=available,
        )


class InsufficientStockError(ItemError):
    """物品库存不足"""

    error_code = "INSUFFICIENT_STOCK"

    def __init__(
        self,
        item_name: str,
        required: int,
        available: int,
        message: str | None = None,
    ):
        if message is None:
            message = f"{item_name}数量不足，需要 {required}，当前 {available}"
        super().__init__(
            message,
            item_name=item_name,
            required=required,
            available=available,
        )


class ItemNotConfiguredError(ItemError):
    """物品未配置奖励"""

    error_code = "ITEM_NOT_CONFIGURED"
    default_message = "物品配置异常，请联系管理员"

    def __init__(self, detail: str | None = None, *, message: str | None = None):
        public_message = message
        if public_message is None:
            has_english = any("A" <= char <= "Z" or "a" <= char <= "z" for char in detail) if detail else False
            public_message = detail if detail and not has_english else self.default_message
        super().__init__(public_message, detail=detail)


class ItemNotUsableError(ItemError):
    """物品不可使用"""

    error_code = "ITEM_NOT_USABLE"

    def __init__(self, item_name: str | None = None, reason: str | None = None, message: str | None = None):
        if message is None:
            reason_messages = {
                "not_warehouse_usable": "此物品不可在仓库使用",
                "equip_in_guest_detail": "装备道具请在门客详情页为指定门客使用",
                "skill_book": "技能书请在门客详情页为指定门客使用",
                "experience_item": "经验道具请在门客详情页为指定门客使用",
                "medicine": "药品道具请在门客详情页为指定门客使用",
                "magnifying_glass": "放大镜请在候选区使用",
                "unknown_effect": "未知的道具效果",
            }
            message = reason_messages.get(reason or "", "此物品不可使用")
        super().__init__(message, item_name=item_name, reason=reason)


class ItemResourceOverflowConfirmationRequired(ItemError):
    """使用物品会丢失超出容量的资源，需要玩家再次确认。"""

    error_code = "ITEM_RESOURCE_OVERFLOW_CONFIRMATION_REQUIRED"

    def __init__(
        self,
        item_name: str,
        requested_resources: dict[str, int],
        credited_resources: dict[str, int],
        overflow_resources: dict[str, int],
        confirmation_snapshot: dict[str, object],
        *,
        message: str,
    ):
        self.item_name = item_name
        self.requested_resources = dict(requested_resources)
        self.credited_resources = dict(credited_resources)
        self.overflow_resources = dict(overflow_resources)
        self.confirmation_snapshot = dict(confirmation_snapshot)
        super().__init__(
            message,
            item_name=item_name,
            requested_resources=self.requested_resources,
            credited_resources=self.credited_resources,
            overflow_resources=self.overflow_resources,
        )


# ============ 装备相关异常 ============


class EquipmentError(GameError):
    """装备相关错误基类"""

    error_code = "EQUIPMENT_ERROR"


class EquipmentAlreadyEquippedError(EquipmentError):
    """装备已被其他门客使用"""

    error_code = "EQUIPMENT_ALREADY_EQUIPPED"
    default_message = "该装备已被其他门客使用"


class EquipmentNotEquippedError(EquipmentError):
    """装备未被此门客使用"""

    error_code = "EQUIPMENT_NOT_EQUIPPED"
    default_message = "该装备未被此门客使用"


class DuplicateEquipmentError(EquipmentError):
    """同名装备已装备"""

    error_code = "DUPLICATE_EQUIPMENT"
    default_message = "同名装备已装备，无法重复装备"
