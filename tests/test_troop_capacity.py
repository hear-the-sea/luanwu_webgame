from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

import gameplay.services.missions_impl.execution as mission_execution
from battle.models import BattleReport, TroopTemplate
from core.exceptions import TroopCapacityFullError
from gameplay.models import Manor, MissionRun, MissionTemplate, PlayerTroop, TroopBankStorage
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions import refresh_mission_runs
from gameplay.services.missions_impl.finalization_helpers import return_attacker_troops_after_mission
from gameplay.services.raid.combat.troop_ops import _add_troops_batch as add_raid_troops_batch
from gameplay.services.raid.combat.troop_ops import _return_surviving_troops as return_raid_troops
from gameplay.services.recruitment.troops import _add_troops_batch as add_mission_troops_batch


def _create_troop_template(key: str) -> TroopTemplate:
    template, _ = TroopTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": "容量回归护院",
            "description": "",
            "base_attack": 10,
            "base_defense": 10,
            "base_hp": 10,
            "speed_bonus": 0,
            "priority": 0,
            "default_count": 0,
        },
    )
    return template


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "add_troops_batch",
    [add_mission_troops_batch, add_raid_troops_batch],
    ids=["mission-return", "raid-return"],
)
def test_direct_battle_and_mission_troop_adds_enforce_manor_capacity(django_user_model, add_troops_batch):
    user = django_user_model.objects.create_user(username=f"troop_return_{add_troops_batch.__module__}")
    manor = ensure_manor(user)
    template = _create_troop_template(f"capacity_return_{add_troops_batch.__module__.split('.')[-1]}")
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=7999)

    with pytest.raises(TroopCapacityFullError, match="庄园内护院容量不足"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            add_troops_batch(locked_manor, {template.key: 2})

    assert PlayerTroop.objects.get(manor=manor, troop_template=template).count == 7999


@pytest.mark.django_db
@pytest.mark.parametrize("return_path", ["mission", "raid"])
def test_returned_troops_over_manor_capacity_use_troop_bank(django_user_model, return_path):
    user = django_user_model.objects.create_user(username=f"troop_return_bank_{return_path}", password="pass123")
    manor = ensure_manor(user)
    template = _create_troop_template(f"return_bank_{return_path}")
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=8000)

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        if return_path == "mission":
            run = SimpleNamespace(
                mission=SimpleNamespace(is_defense=False),
                troop_loadout={template.key: 520},
                is_retreating=False,
                manor=locked_manor,
                id=1,
            )
            return_attacker_troops_after_mission(run, None, logger=SimpleNamespace(warning=lambda *_a, **_k: None))
        else:
            return_raid_troops(
                SimpleNamespace(
                    attacker=locked_manor,
                    battle_report=None,
                    troop_loadout={template.key: 520},
                )
            )

    assert PlayerTroop.objects.get(manor=manor, troop_template=template).count == 8000
    assert TroopBankStorage.objects.get(manor=manor, troop_template=template).count == 520


@pytest.mark.django_db
def test_returned_troops_use_available_space_then_discard_overflow(django_user_model):
    user = django_user_model.objects.create_user(username="troop_return_bank_full", password="pass123")
    manor = ensure_manor(user)
    return_template = _create_troop_template("return_bank_partial")
    bank_template = _create_troop_template("return_bank_reserve")
    PlayerTroop.objects.create(manor=manor, troop_template=return_template, count=7990)
    TroopBankStorage.objects.create(
        manor=manor,
        troop_template=bank_template,
        count=4990,
    )

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        return_raid_troops(
            SimpleNamespace(
                attacker=locked_manor,
                battle_report=None,
                troop_loadout={return_template.key: 30},
            )
        )

    assert PlayerTroop.objects.get(manor=manor, troop_template=return_template).count == 8000
    assert sum(TroopBankStorage.objects.filter(manor=manor).values_list("count", flat=True)) == 5000
    assert TroopBankStorage.objects.get(manor=manor, troop_template=return_template).count == 10


@pytest.mark.django_db
def test_due_mission_settles_when_returned_troops_exceed_manor_capacity(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="mission_return_over_capacity", password="pass123")
    manor = ensure_manor(user)
    troop_template = _create_troop_template("mission_return_over_capacity")
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=8000)
    mission = MissionTemplate.objects.create(
        key="mission_return_over_capacity",
        name="护院超容量返程测试",
        is_defense=False,
    )
    now = timezone.now()
    report = BattleReport.objects.create(
        manor=manor,
        opponent_name="测试目标",
        battle_type="task",
        attacker_team=[],
        defender_team=[],
        losses={"attacker": {"casualties": [], "hp_updates": {}}},
        winner="defender",
        starts_at=now,
        completed_at=now,
    )
    run = MissionRun.objects.create(
        manor=manor,
        mission=mission,
        battle_report=report,
        troop_loadout={troop_template.key: 520},
        return_at=now - timedelta(seconds=1),
    )
    monkeypatch.setattr(mission_execution, "create_mission_report_message", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(mission_execution, "notify_mission_report", lambda *_args, **_kwargs: None)

    assert refresh_mission_runs(manor, prefer_async=False) == 1

    run.refresh_from_db()
    assert run.status == MissionRun.Status.COMPLETED
    assert run.completed_at is not None
    assert PlayerTroop.objects.get(manor=manor, troop_template=troop_template).count == 8000
    assert TroopBankStorage.objects.get(manor=manor, troop_template=troop_template).count == 520
