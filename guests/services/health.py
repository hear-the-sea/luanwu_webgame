"""
门客生命值管理服务
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Dict

from django.db import transaction
from django.utils import timezone

from core.exceptions import (
    GuestFullHpError,
    GuestItemConfigurationError,
    GuestItemOwnershipError,
    GuestNotIdleError,
    GuestOwnershipError,
    InsufficientStockError,
    InvalidHealAmountError,
)
from core.utils.time_scale import scale_value
from gameplay.services.inventory import core as inventory_core

from .. import guest_health_rules as _guest_health_rules
from ..constants import TimeConstants
from ..models import Guest, GuestStatus
from .loyalty import apply_injury_loyalty_decay, clear_injury_loyalty_decay
from .status import GUEST_STATUS_UPDATE_FIELDS, prepare_guest_status_transition, schedule_resumed_guest_training

if TYPE_CHECKING:
    from gameplay.models import InventoryItem, Manor


INJURY_RECOVERY_THRESHOLD = _guest_health_rules.INJURY_RECOVERY_THRESHOLD
INJURED_RECOVERY_RATE_FACTOR = _guest_health_rules.INJURED_RECOVERY_RATE_FACTOR


@dataclass(frozen=True, slots=True)
class MedicineUseQuote:
    manor_id: int
    guest_id: int
    item_id: int
    item_template_id: int
    item_key: str
    heal_amount: int
    item_quantity_before: int
    current_hp_before: int
    max_hp: int
    status_before: str
    healed: int
    new_hp: int
    injury_cured: bool
    status_after: str

    def __post_init__(self) -> None:
        for field in ("manor_id", "guest_id", "item_id", "item_template_id"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if not isinstance(self.item_key, str) or not self.item_key.strip():
            raise ValueError("item_key must be a non-empty string")
        for field in ("heal_amount", "item_quantity_before", "max_hp", "healed"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if (
            isinstance(self.current_hp_before, bool)
            or not isinstance(self.current_hp_before, int)
            or self.current_hp_before < 0
            or self.current_hp_before >= self.max_hp
        ):
            raise ValueError("current_hp_before must be below max_hp")
        if self.new_hp != self.current_hp_before + self.healed or not (
            self.current_hp_before < self.new_hp <= self.max_hp
        ):
            raise ValueError("medicine quote has an invalid projected HP result")
        allowed_statuses = {str(GuestStatus.IDLE), str(GuestStatus.INJURED)}
        if self.status_before not in allowed_statuses or self.status_after not in allowed_statuses:
            raise ValueError("medicine quote has an invalid guest status")
        expected_status = str(GuestStatus.IDLE) if self.injury_cured else self.status_before
        if self.status_after != expected_status:
            raise ValueError("medicine quote has an invalid projected guest status")
        if self.injury_cured and self.status_before != str(GuestStatus.INJURED):
            raise ValueError("only an injured guest can be cured")

    def to_payload(self) -> dict[str, object]:
        return {
            "current_hp_before": self.current_hp_before,
            "guest_id": self.guest_id,
            "heal_amount": self.heal_amount,
            "healed": self.healed,
            "injury_cured": self.injury_cured,
            "item_id": self.item_id,
            "item_key": self.item_key,
            "item_quantity_before": self.item_quantity_before,
            "item_template_id": self.item_template_id,
            "manor_id": self.manor_id,
            "max_hp": self.max_hp,
            "new_hp": self.new_hp,
            "status_after": self.status_after,
            "status_before": self.status_before,
        }


@dataclass(slots=True)
class _MedicineStock:
    """批量疗伤过程中已经锁定的一种药品库存。"""

    item: Any
    heal_amount: int


def _normalize_non_negative_health_int(raw_value: Any, *, contract_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    raw_for_int: Any = raw_value
    try:
        parsed_value = int(raw_for_int)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}") from exc
    if parsed_value < 0:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return parsed_value


def _normalize_positive_health_int(raw_value: Any, *, contract_name: str) -> int:
    value = _normalize_non_negative_health_int(raw_value, contract_name=contract_name)
    if value <= 0:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return value


def _normalize_positive_medicine_int(raw_value: Any) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise GuestItemConfigurationError("道具未配置有效恢复值")
    try:
        parsed_value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise GuestItemConfigurationError("道具未配置有效恢复值") from exc
    if parsed_value <= 0:
        raise GuestItemConfigurationError("道具未配置有效恢复值")
    return parsed_value


def resolve_medicine_heal_amount(item: InventoryItem) -> int:
    """按统一领域规则解析药品的 HP 恢复量。"""
    payload = item.template.effect_payload
    if not isinstance(payload, dict):
        raise GuestItemConfigurationError("道具未配置有效恢复值")
    return _normalize_positive_medicine_int(payload.get("hp"))


def quote_medicine_item_for_guest(
    manor: Manor,
    guest: Guest,
    item: InventoryItem,
) -> MedicineUseQuote:
    """只读校验一次药品治疗，并冻结其领域输入与预期结果。"""
    from gameplay.models import InventoryItem as InventoryItemModel
    from gameplay.models import ItemTemplate

    if (
        not getattr(item, "pk", None)
        or int(item.manor_id) != int(manor.pk)
        or item.storage_location != InventoryItemModel.StorageLocation.WAREHOUSE
        or item.template.effect_type != ItemTemplate.EffectType.MEDICINE
    ):
        raise GuestItemOwnershipError()
    item_quantity = int(item.quantity or 0)
    if item_quantity <= 0:
        raise InsufficientStockError(item.template.name, 1, item_quantity)
    if not getattr(guest, "pk", None) or int(guest.manor_id) != int(manor.pk):
        raise GuestOwnershipError(message="门客不存在或不属于您的庄园")

    heal_amount = resolve_medicine_heal_amount(item)
    if guest.status not in {GuestStatus.IDLE, GuestStatus.INJURED}:
        raise GuestNotIdleError(guest)
    if int(guest.current_hp) >= int(guest.max_hp):
        raise GuestFullHpError(guest)

    current_hp = int(guest.current_hp)
    max_hp = int(guest.max_hp)
    new_hp = min(max_hp, current_hp + heal_amount)
    injury_cured = bool(
        guest.status == GuestStatus.INJURED
        and _guest_health_rules.should_clear_injured_status(
            current_hp=new_hp,
            max_hp=max_hp,
        )
    )
    return MedicineUseQuote(
        manor_id=int(manor.pk),
        guest_id=int(guest.pk),
        item_id=int(item.pk),
        item_template_id=int(item.template_id),
        item_key=str(item.template.key),
        heal_amount=heal_amount,
        item_quantity_before=item_quantity,
        current_hp_before=current_hp,
        max_hp=max_hp,
        status_before=str(guest.status),
        healed=new_hp - current_hp,
        new_hp=new_hp,
        injury_cured=injury_cured,
        status_after=(str(GuestStatus.IDLE) if injury_cured else str(guest.status)),
    )


def recover_guest_hp(guest: Guest, now: datetime | None = None) -> None:
    """
    恢复门客生命值。

    从1点到满血耗时24小时，每10分钟检查一次并线性恢复。
    澡堂建筑可提供生命恢复加成（满级200%）。

    重伤门客（INJURED状态）会自动恢复，但速率仅为普通状态的 1/10；自然回血达到最大生命值20%后解除重伤。
    全局时间流速（GAME_TIME_MULTIPLIER）同样作用于重伤回血。
    """
    now = now or timezone.now()

    last = guest.last_hp_recovery_at or guest.created_at or now
    injury_decay_intervals = apply_injury_loyalty_decay(guest, now=now)
    loyalty_update_fields = ["loyalty", "injury_loyalty_processed_at"] if injury_decay_intervals > 0 else []
    if guest.current_hp >= guest.max_hp:
        update_fields = list(loyalty_update_fields)
        resumed_training = False
        if last != now:
            guest.last_hp_recovery_at = now
            update_fields.append("last_hp_recovery_at")
        # 修复：满血时不应继续显示重伤状态
        if guest.status == GuestStatus.INJURED:
            transition = prepare_guest_status_transition(guest, GuestStatus.IDLE, now=now)
            resumed_training = transition.resumed_training
            update_fields.extend(GUEST_STATUS_UPDATE_FIELDS)
            clear_injury_loyalty_decay(guest)
            if "injury_loyalty_processed_at" not in update_fields:
                update_fields.append("injury_loyalty_processed_at")
        if update_fields:
            guest.save(update_fields=list(dict.fromkeys(update_fields)))
        if resumed_training:
            schedule_resumed_guest_training(guest, source="injury_recovery")
        return
    elapsed = (now - last).total_seconds()
    if elapsed < TimeConstants.HP_RECOVERY_INTERVAL:
        if loyalty_update_fields:
            guest.save(update_fields=loyalty_update_fields)
        return
    per_second = max(1, (guest.max_hp - 1) / TimeConstants.HP_FULL_RECOVERY_TIME)

    # 应用澡堂加成
    hp_multiplier = 1.0
    if hasattr(guest, "manor") and guest.manor:
        hp_multiplier = guest.manor.hp_recovery_multiplier
    guest.current_hp, intervals = _guest_health_rules.compute_recovered_hp(
        current_hp=guest.current_hp,
        max_hp=guest.max_hp,
        elapsed_seconds=elapsed,
        recovery_interval_seconds=TimeConstants.HP_RECOVERY_INTERVAL,
        scaled_recovery_per_second=scale_value(per_second),
        hp_multiplier=hp_multiplier,
        is_injured=guest.status == GuestStatus.INJURED,
        injured_recovery_rate_factor=INJURED_RECOVERY_RATE_FACTOR,
    )
    if intervals <= 0:
        return
    guest.last_hp_recovery_at = last + timedelta(seconds=intervals * TimeConstants.HP_RECOVERY_INTERVAL)
    update_fields = ["current_hp", "last_hp_recovery_at", *loyalty_update_fields]
    resumed_training = False
    if guest.status == GuestStatus.INJURED and _guest_health_rules.should_clear_injured_status(
        current_hp=guest.current_hp,
        max_hp=guest.max_hp,
    ):
        transition = prepare_guest_status_transition(guest, GuestStatus.IDLE, now=now)
        resumed_training = transition.resumed_training
        update_fields.extend(GUEST_STATUS_UPDATE_FIELDS)
        clear_injury_loyalty_decay(guest)
        if "injury_loyalty_processed_at" not in update_fields:
            update_fields.append("injury_loyalty_processed_at")
    guest.save(update_fields=list(dict.fromkeys(update_fields)))
    if resumed_training:
        schedule_resumed_guest_training(guest, source="injury_recovery")


@transaction.atomic
def recover_guest_hp_for_guest(
    guest_id: int,
    *,
    now: datetime | None = None,
) -> bool:
    """锁定并重新校验单个门客后，提交一次被动回血。"""
    recovery_now = now or timezone.now()
    guest = Guest.objects.select_for_update().filter(pk=guest_id).first()
    if guest is None or guest.status not in {GuestStatus.IDLE, GuestStatus.INJURED}:
        return False

    last_recovery_at = guest.last_hp_recovery_at
    cutoff = recovery_now - timedelta(seconds=TimeConstants.HP_RECOVERY_INTERVAL)
    if last_recovery_at is None or last_recovery_at > cutoff:
        return False

    max_hp = guest.max_hp
    if guest.current_hp >= max_hp and guest.status != GuestStatus.INJURED:
        return False

    before_state = (
        guest.current_hp,
        guest.last_hp_recovery_at,
        guest.status,
        guest.loyalty,
        guest.injury_loyalty_processed_at,
    )
    recover_guest_hp(guest, now=recovery_now)
    after_state = (
        guest.current_hp,
        guest.last_hp_recovery_at,
        guest.status,
        guest.loyalty,
        guest.injury_loyalty_processed_at,
    )
    return after_state != before_state


def heal_guest(guest: Guest, heal_amount: int) -> dict:
    """
    为门客治疗，恢复生命值。

    如果门客处于重伤状态且治疗后HP达到阈值（当前为20%）以上，自动解除重伤状态。

    Args:
        guest: 门客实例
        heal_amount: 治疗量

    Returns:
        包含治疗结果的字典：
        - healed: 实际恢复的HP
        - new_hp: 治疗后的HP
        - injury_cured: 是否解除了重伤状态

    Raises:
        InvalidHealAmountError: 治疗量无效
        GuestFullHpError: 门客已满血
    """
    if guest.status not in {GuestStatus.IDLE, GuestStatus.INJURED}:
        raise GuestNotIdleError(guest)
    if heal_amount <= 0:
        raise InvalidHealAmountError()
    if guest.current_hp >= guest.max_hp:
        raise GuestFullHpError(guest)

    now = timezone.now()
    injury_decay_intervals = apply_injury_loyalty_decay(guest, now=now)
    old_hp = guest.current_hp
    new_hp = min(guest.max_hp, guest.current_hp + heal_amount)
    healed = new_hp - old_hp

    guest.current_hp = new_hp
    guest.last_hp_recovery_at = now

    update_fields = ["current_hp", "last_hp_recovery_at"]
    if injury_decay_intervals > 0:
        update_fields.extend(["loyalty", "injury_loyalty_processed_at"])
    injury_cured = False

    # 检查是否解除重伤状态
    resumed_training = False
    if guest.status == GuestStatus.INJURED:
        if _guest_health_rules.should_clear_injured_status(current_hp=new_hp, max_hp=guest.max_hp):
            transition = prepare_guest_status_transition(guest, GuestStatus.IDLE, now=now)
            resumed_training = transition.resumed_training
            update_fields.extend(GUEST_STATUS_UPDATE_FIELDS)
            clear_injury_loyalty_decay(guest)
            if "injury_loyalty_processed_at" not in update_fields:
                update_fields.append("injury_loyalty_processed_at")
            injury_cured = True

    guest.save(update_fields=list(dict.fromkeys(update_fields)))
    if resumed_training:
        schedule_resumed_guest_training(guest, source="injury_heal")

    return {
        "healed": healed,
        "new_hp": new_hp,
        "injury_cured": injury_cured,
    }


def _load_locked_medicine_item(manor: Manor, item_id: int) -> InventoryItem:
    from gameplay.models import InventoryItem, ItemTemplate

    locked_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            pk=item_id,
            manor=manor,
            template__effect_type=ItemTemplate.EffectType.MEDICINE,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
    if not locked_item:
        raise GuestItemOwnershipError()
    if locked_item.quantity <= 0:
        raise InsufficientStockError(locked_item.template.name, 1, locked_item.quantity)
    return locked_item


def validate_medicine_use_quote(
    expected: MedicineUseQuote,
    actual: MedicineUseQuote,
) -> None:
    if not isinstance(expected, MedicineUseQuote) or not isinstance(actual, MedicineUseQuote):
        raise TypeError("medicine quotes must be MedicineUseQuote values")
    if actual != expected:
        raise GuestItemConfigurationError("药品使用条件已变化，请重试")


def apply_medicine_item_for_guest_locked(
    manor: Manor,
    guest_id: int,
    item_id: int,
    *,
    expected_quote: MedicineUseQuote | None = None,
    expected_heal_amount: int | None = None,
) -> Dict[str, Any]:
    """在已锁 Manor 的事务中，按 InventoryItem -> Guest 顺序提交治疗。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("apply_medicine_item_for_guest_locked must be called inside transaction.atomic()")
    if expected_quote is not None and (
        expected_quote.manor_id != int(manor.pk)
        or expected_quote.guest_id != int(guest_id)
        or expected_quote.item_id != int(item_id)
    ):
        raise GuestItemConfigurationError("药品使用条件已变化，请重试")

    locked_item = _load_locked_medicine_item(manor, item_id)
    locked_guest = Guest.objects.select_for_update().select_related("template").filter(pk=guest_id, manor=manor).first()
    if not locked_guest:
        raise GuestOwnershipError(message="门客不存在或不属于您的庄园")

    quote = quote_medicine_item_for_guest(manor, locked_guest, locked_item)
    if expected_heal_amount is not None and quote.heal_amount != (
        _normalize_positive_medicine_int(expected_heal_amount)
    ):
        raise GuestItemConfigurationError("药品使用条件已变化，请重试")
    if expected_quote is not None:
        validate_medicine_use_quote(expected_quote, quote)

    raw_result = heal_guest(locked_guest, quote.heal_amount)
    healed = _normalize_non_negative_health_int(
        raw_result.get("healed"),
        contract_name="medicine healed",
    )
    if (
        healed != quote.healed
        or int(locked_guest.current_hp) != quote.new_hp
        or str(locked_guest.status) != quote.status_after
        or bool(raw_result.get("injury_cured", False)) != quote.injury_cured
    ):
        raise AssertionError("medicine application differed from its quote")

    inventory_core.consume_inventory_item_locked(locked_item, 1)
    remaining_quantity = (
        _normalize_non_negative_health_int(
            locked_item.quantity,
            contract_name="medicine remaining_item_quantity",
        )
        if locked_item.pk
        else 0
    )
    return {
        "healed": healed,
        "new_hp": _normalize_non_negative_health_int(
            locked_guest.current_hp,
            contract_name="medicine new_hp",
        ),
        "max_hp": _normalize_positive_health_int(
            locked_guest.max_hp,
            contract_name="medicine max_hp",
        ),
        "status": locked_guest.status,
        "status_display": locked_guest.get_status_display(),
        "injury_cured": quote.injury_cured,
        "training_eta": locked_guest.training_complete_at,
        "training_paused": locked_guest.training_is_paused,
        "remaining_item_quantity": remaining_quantity,
    }


@transaction.atomic
def use_medicine_item_for_guest(
    manor: Manor,
    guest: Guest,
    item_id: int,
    heal_amount: int | None = None,
) -> Dict[str, Any]:
    """
    对单个门客使用药品（原子化版本）。

    关键保证：
    - 治疗效果与道具扣减在同一事务中完成
    - 任一步失败都会整体回滚，避免“先生效后扣失败”导致状态不一致
    - 锁顺序统一为 Manor -> InventoryItem -> Guest
    """
    from gameplay.models import Manor as ManorModel

    locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)
    return apply_medicine_item_for_guest_locked(
        locked_manor,
        int(guest.pk),
        item_id,
        expected_heal_amount=heal_amount,
    )


def _select_batch_medicine_stock(
    stocks: list[_MedicineStock],
    *,
    missing_hp: int,
) -> _MedicineStock | None:
    """为一次治疗选择最合适的药品，避免在满血前浪费大剂量药品。

    优先选择能够刚好覆盖缺口且溢出最少的药品；没有单瓶足够时，
    使用当前库存中恢复量最大的药品，确保有限库存优先转化为实际 HP。
    同恢复量时按模板 key 和库存 id 排序，保证批量操作可复现。
    """

    available = [
        stock
        for stock in stocks
        if getattr(stock.item, "pk", None) and int(getattr(stock.item, "quantity", 0) or 0) > 0
    ]
    if not available or missing_hp <= 0:
        return None

    finishing = [stock for stock in available if stock.heal_amount >= missing_hp]
    if finishing:
        return min(
            finishing,
            key=lambda stock: (
                stock.heal_amount - missing_hp,
                stock.heal_amount,
                str(stock.item.template.key),
                int(stock.item.pk),
            ),
        )
    return min(
        available,
        key=lambda stock: (
            -stock.heal_amount,
            str(stock.item.template.key),
            int(stock.item.pk),
        ),
    )


def _batch_healing_guest_sort_key(guest: Guest) -> tuple[int, Fraction, int, int]:
    """重伤优先，其次优先处理缺血比例更高的门客。"""

    max_hp = max(1, int(guest.max_hp))
    current_hp = max(0, min(max_hp, int(guest.current_hp)))
    missing_hp = max_hp - current_hp
    return (
        0 if guest.status == GuestStatus.INJURED else 1,
        -Fraction(missing_hp, max_hp),
        -missing_hp,
        int(guest.pk),
    )


@transaction.atomic
def heal_all_guests_with_medicine(manor: Manor) -> dict[str, int]:
    """使用仓库药品批量治疗庄园内可治疗的门客。

    批量操作始终先锁庄园，再锁住参与治疗的门客和药品；庄园父行锁会
    与单个药品使用串行化，避免重复扣减。每位门客会被持续治疗到满血
    或药品耗尽；药品不足时按“重伤优先、缺血比例优先”部分完成，并
    把未完成数量返回给调用方。
    """

    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.models import Manor as ManorModel

    locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)
    locked_guests = list(
        Guest.objects.select_for_update()
        .select_related("template")
        .filter(
            manor_id=locked_manor.pk,
            status__in=[GuestStatus.IDLE, GuestStatus.INJURED],
        )
        .order_by("id")
    )
    healable_guests = [guest for guest in locked_guests if int(guest.current_hp) < int(guest.max_hp)]
    requested_count = len(healable_guests)
    if not healable_guests:
        return {
            "requested_count": 0,
            "healed_count": 0,
            "partial_count": 0,
            "unhealed_count": 0,
            "consumed_item_count": 0,
            "healed_hp": 0,
        }

    locked_items = list(
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            manor_id=locked_manor.pk,
            quantity__gt=0,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            template__effect_type=ItemTemplate.EffectType.MEDICINE,
        )
        .order_by("template__key", "id")
    )
    stocks: list[_MedicineStock] = []
    for item in locked_items:
        try:
            heal_amount = resolve_medicine_heal_amount(item)
        except GuestItemConfigurationError:
            # 单个异常配置不应阻断其它正常药品的批量治疗。
            continue
        stocks.append(_MedicineStock(item=item, heal_amount=heal_amount))

    healed_count = 0
    partial_count = 0
    consumed_item_count = 0
    healed_hp = 0
    for guest in sorted(healable_guests, key=_batch_healing_guest_sort_key):
        used_for_guest = False
        while int(guest.current_hp) < int(guest.max_hp):
            missing_hp = int(guest.max_hp) - int(guest.current_hp)
            stock = _select_batch_medicine_stock(stocks, missing_hp=missing_hp)
            if stock is None:
                break
            try:
                # 为单次尝试建立保存点：若库存行在重新校验时已失效，
                # 只回滚这次尝试，不让前面成功的治疗被污染。
                with transaction.atomic():
                    result = apply_medicine_item_for_guest_locked(
                        locked_manor,
                        int(guest.pk),
                        int(stock.item.pk),
                        expected_heal_amount=stock.heal_amount,
                    )
            except (GuestFullHpError, GuestItemConfigurationError, GuestItemOwnershipError, GuestNotIdleError):
                # 状态变化或库存配置变化时跳过当前药品，继续尝试其它库存。
                stock.item.quantity = 0
                stock.item.pk = None
                continue
            except (GuestOwnershipError, InsufficientStockError):
                stock.item.quantity = 0
                stock.item.pk = None
                continue

            healed = int(result["healed"])
            if healed <= 0:
                break
            used_for_guest = True
            consumed_item_count += 1
            healed_hp += healed
            guest.current_hp = int(result["new_hp"])
            guest.status = str(result["status"])
            remaining_quantity = int(result["remaining_item_quantity"])
            if remaining_quantity <= 0:
                stock.item.quantity = 0
                stock.item.pk = None
            else:
                stock.item.quantity = remaining_quantity

        if int(guest.current_hp) >= int(guest.max_hp):
            healed_count += 1
        elif used_for_guest:
            partial_count += 1

    return {
        "requested_count": requested_count,
        "healed_count": healed_count,
        "partial_count": partial_count,
        "unhealed_count": requested_count - healed_count,
        "consumed_item_count": consumed_item_count,
        "healed_hp": healed_hp,
    }
