from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from django.db import connection, transaction
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import TroopRecruitmentError
from gameplay.models import InventoryItem, Manor, PlayerTroop, TroopRecruitment
from gameplay.services.recruitment.recruitment import quote_troop_recruitment, recruit_troops_locked
from tests.troop_recruitment_service.support import create_tool_template, set_inventory

pytest_plugins = ("tests.troop_recruitment_service.fixtures",)


def _prepare_scout_equipment(manor: Manor, quantity: int) -> InventoryItem:
    horse = create_tool_template("equip_zaohongma", "枣红马")
    set_inventory(manor, horse, quantity)
    return InventoryItem.objects.get(
        manor=manor,
        template=horse,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


@pytest.mark.django_db
def test_quote_troop_recruitment_is_read_only_and_immutable(recruit_manor):
    item = _prepare_scout_equipment(recruit_manor, 3)
    write_statements: list[str] = []

    def _capture_writes(execute, sql, params, many, context):
        verb = sql.lstrip().split(None, 1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            write_statements.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(_capture_writes):
        quote = quote_troop_recruitment(recruit_manor, "scout", quantity=2)

    assert write_statements == []
    assert quote.equipment_costs == (("equip_zaohongma", 2),)
    assert quote.equipment_stock == (("equip_zaohongma", 3),)
    assert quote.to_payload()["equipment_costs"] == {"equip_zaohongma": 2}
    with pytest.raises(FrozenInstanceError):
        quote.quantity = 3
    item.refresh_from_db()
    assert item.quantity == 3


@pytest.mark.django_db(transaction=True)
def test_recruit_troops_locked_requires_outer_transaction(recruit_manor):
    _prepare_scout_equipment(recruit_manor, 1)
    quote = quote_troop_recruitment(recruit_manor, "scout")

    with pytest.raises(RuntimeError, match="inside transaction.atomic"):
        recruit_troops_locked(recruit_manor, quote)


@pytest.mark.django_db
def test_recruit_troops_locked_completes_audit_and_does_not_schedule(
    monkeypatch,
    recruit_manor,
):
    item = _prepare_scout_equipment(recruit_manor, 3)
    quote = quote_troop_recruitment(recruit_manor, "scout", quantity=2)
    fixed_now = timezone.now()
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._schedule_recruitment_completion",
        lambda *_args, **_kwargs: pytest.fail("synchronous recruitment scheduled Celery"),
    )

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=recruit_manor.pk)
        recruitment = recruit_troops_locked(
            locked_manor,
            quote,
            now=fixed_now,
        )

    recruitment.refresh_from_db()
    recruit_manor.refresh_from_db(fields=["retainer_count"])
    item.refresh_from_db(fields=["quantity"])
    troop_template = TroopTemplate.objects.get(key="scout")
    player_troop = PlayerTroop.objects.get(
        manor=recruit_manor,
        troop_template=troop_template,
    )

    assert recruitment.status == TroopRecruitment.Status.COMPLETED
    assert recruitment.complete_at == fixed_now
    assert recruitment.finished_at == fixed_now
    assert recruitment.actual_duration == quote.actual_duration
    assert recruitment.equipment_costs == {"equip_zaohongma": 2}
    assert item.quantity == 1
    assert recruit_manor.retainer_count == 18
    assert player_troop.count == 2


@pytest.mark.django_db
def test_recruit_troops_locked_rejects_stale_quote_before_writes(recruit_manor):
    item = _prepare_scout_equipment(recruit_manor, 2)
    quote = quote_troop_recruitment(recruit_manor, "scout")
    InventoryItem.objects.filter(pk=item.pk).update(quantity=3)

    with pytest.raises(TroopRecruitmentError, match="报价已失效"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=recruit_manor.pk)
            recruit_troops_locked(locked_manor, quote)

    recruit_manor.refresh_from_db(fields=["retainer_count"])
    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 3
    assert recruit_manor.retainer_count == 20
    assert not TroopRecruitment.objects.filter(manor=recruit_manor).exists()
    assert not PlayerTroop.objects.filter(manor=recruit_manor).exists()


@pytest.mark.django_db
def test_recruit_troops_locked_rechecks_missing_inventory(recruit_manor):
    item = _prepare_scout_equipment(recruit_manor, 1)
    quote = quote_troop_recruitment(recruit_manor, "scout")
    item.delete()

    with pytest.raises(TroopRecruitmentError, match="不足"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=recruit_manor.pk)
            recruit_troops_locked(locked_manor, quote)

    recruit_manor.refresh_from_db(fields=["retainer_count"])
    assert recruit_manor.retainer_count == 20
    assert not TroopRecruitment.objects.filter(manor=recruit_manor).exists()
    assert not PlayerTroop.objects.filter(manor=recruit_manor).exists()


@pytest.mark.django_db
def test_recruit_troops_locked_rolls_back_consumption_and_audit_on_failure(
    monkeypatch,
    recruit_manor,
):
    item = _prepare_scout_equipment(recruit_manor, 2)
    quote = quote_troop_recruitment(recruit_manor, "scout")
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.apply_troop_recruitment_result_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced recruitment failure")),
    )

    with pytest.raises(RuntimeError, match="forced recruitment failure"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=recruit_manor.pk)
            recruit_troops_locked(locked_manor, quote)

    recruit_manor.refresh_from_db(fields=["retainer_count"])
    item.refresh_from_db(fields=["quantity"])
    assert item.quantity == 2
    assert recruit_manor.retainer_count == 20
    assert not TroopRecruitment.objects.filter(manor=recruit_manor).exists()
    assert not PlayerTroop.objects.filter(manor=recruit_manor).exists()
