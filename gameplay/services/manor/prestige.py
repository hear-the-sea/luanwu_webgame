"""
声望系统服务模块

玩家每累计花费1000两白银在建筑或技术上，增长1点声望。
"""

from __future__ import annotations

from django.db import transaction
from django.dispatch import Signal

from ...models import Manor

# 每增加1点声望需要花费的银两
PRESTIGE_SILVER_THRESHOLD = 1000

# 领域写入只发布提交后的声望事实；虚拟人口接线由 gameplay.signals 消费。
prestige_change_committed = Signal()


def _emit_prestige_change_committed(
    *,
    manor_id: int,
    region: str,
    before_prestige: int,
    after_prestige: int,
) -> None:
    prestige_change_committed.send(
        sender=Manor,
        manor_id=manor_id,
        region=region,
        before_prestige=before_prestige,
        after_prestige=after_prestige,
    )


def schedule_prestige_change_on_commit(
    *,
    manor: Manor,
    before_prestige: int,
    after_prestige: int,
) -> bool:
    """Publish a committed prestige change without coupling the domain owner to consumers."""
    before = int(before_prestige)
    after = int(after_prestige)
    if before == after:
        return False
    if manor.pk is None:
        raise ValueError("manor must be persisted before scheduling a prestige change")
    manor_id = int(manor.pk)
    region = str(manor.region)

    def _emit_after_commit() -> None:
        _emit_prestige_change_committed(
            manor_id=manor_id,
            region=region,
            before_prestige=before,
            after_prestige=after,
        )

    transaction.on_commit(_emit_after_commit, robust=True)
    return True


def add_prestige_silver_locked(manor: Manor, silver_spent: int) -> int:
    """
    累计花费银两并计算声望增长（假设调用方已在 transaction.atomic 中持有 manor 行锁）。
    """
    if silver_spent <= 0:
        return 0
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("add_prestige_silver_locked must be called inside transaction.atomic()")

    before_total = int(manor.prestige)
    before_spent = manor.prestige_silver_spent
    before_spending_prestige = before_spent // PRESTIGE_SILVER_THRESHOLD

    # 历史上 prestige 字段可能包含 PVP 声望，这里将其视为“PVP附加声望”
    # spending 声望始终由累计消费推导，不应被PVP输赢影响。
    current_pvp_prestige = max(0, manor.prestige - before_spending_prestige)

    manor.prestige_silver_spent = before_spent + silver_spent
    after_spending_prestige = manor.prestige_silver_spent // PRESTIGE_SILVER_THRESHOLD

    manor.prestige = after_spending_prestige + current_pvp_prestige
    manor.save(update_fields=["prestige_silver_spent", "prestige"])
    schedule_prestige_change_on_commit(
        manor=manor,
        before_prestige=before_total,
        after_prestige=int(manor.prestige),
    )

    gained = after_spending_prestige - before_spending_prestige
    return max(0, gained)


def add_prestige_silver(manor: Manor, silver_spent: int) -> int:
    """
    累计花费银两并计算声望增长。

    Args:
        manor: 庄园实例
        silver_spent: 本次花费的银两数量

    Returns:
        本次获得的声望点数
    """
    if silver_spent <= 0:
        return 0

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        gained = add_prestige_silver_locked(locked_manor, silver_spent)
        manor.refresh_from_db(fields=["prestige_silver_spent", "prestige"])
        return gained


def get_prestige_progress(manor: Manor) -> dict:
    """
    获取声望进度信息。

    Args:
        manor: 庄园实例

    Returns:
        {
            "prestige": 当前声望,
            "silver_spent": 累计花费银两,
            "progress": 当前进度（0-999），
            "threshold": 每点声望所需银两,
        }
    """
    return {
        "prestige": manor.prestige,
        "silver_spent": manor.prestige_silver_spent,
        "progress": manor.prestige_silver_spent % PRESTIGE_SILVER_THRESHOLD,
        "threshold": PRESTIGE_SILVER_THRESHOLD,
    }
