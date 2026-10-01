"""出征护院库存扣除：先用庄园护院，不足部分直接从钱庄带走。"""

from __future__ import annotations

from django.utils import timezone

from core.exceptions import TroopLoadoutError

from ...models import Manor, PlayerTroop, TroopBankStorage


def deduct_dispatch_troops_locked(manor: Manor, loadout: dict[str, int]) -> None:
    """调用方需处于事务内并已锁定 Manor；校验全部兵种后再写入库存。"""

    loadout = {key: count for key, count in loadout.items() if count > 0}
    if not loadout:
        return

    manor_troops = {
        troop.troop_template.key: troop
        for troop in PlayerTroop.objects.select_for_update(of=("self",))
        .filter(manor=manor, troop_template__key__in=loadout)
        .select_related("troop_template")
        .order_by("troop_template_id")
    }
    bank_troops = {
        troop.troop_template.key: troop
        for troop in TroopBankStorage.objects.select_for_update(of=("self",))
        .filter(manor=manor, troop_template__key__in=loadout)
        .select_related("troop_template")
        .order_by("troop_template_id")
    }

    now = timezone.now()
    manor_updates: list[PlayerTroop] = []
    bank_updates: list[TroopBankStorage] = []
    for key, count in loadout.items():
        manor_troop = manor_troops.get(key)
        bank_troop = bank_troops.get(key)
        if manor_troop is None and bank_troop is None:
            raise TroopLoadoutError("护院配置包含无效类型", troop_key=key)

        manor_count = manor_troop.count if manor_troop is not None else 0
        bank_count = bank_troop.count if bank_troop is not None else 0
        if manor_count + bank_count < count:
            troop = manor_troop if manor_troop is not None else bank_troop
            assert troop is not None
            raise TroopLoadoutError(f"护院 {troop.troop_template.name} 数量不足（含钱庄库存）")

        from_manor = min(count, manor_count)
        if manor_troop is not None and from_manor:
            manor_troop.count -= from_manor
            manor_troop.updated_at = now
            manor_updates.append(manor_troop)

        from_bank = count - from_manor
        if bank_troop is not None and from_bank:
            bank_troop.count -= from_bank
            bank_troop.updated_at = now
            bank_updates.append(bank_troop)

    if manor_updates:
        PlayerTroop.objects.bulk_update(manor_updates, ["count", "updated_at"])
    if bank_updates:
        TroopBankStorage.objects.bulk_update(bank_updates, ["count", "updated_at"])
