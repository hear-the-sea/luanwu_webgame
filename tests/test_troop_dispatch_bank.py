from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import RaidStartError, TroopLoadoutError
from gameplay.models import Manor, PlayerTroop, RaidRun, TroopBankStorage, TroopRecruitment
from gameplay.services.manor import troop_bank
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import troop_ops
from gameplay.services.raid.combat.failure import fail_raid_run_and_release_resources
from gameplay.services.recruitment import troops as mission_troops
from gameplay.services.recruitment.queries import get_player_troops


@pytest.fixture
def inventory(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="dispatch_bank"))
    PlayerTroop.objects.filter(manor=manor).update(count=0)
    templates = {
        key: TroopTemplate.objects.create(
            key=key,
            name=key,
            base_attack=10,
            base_defense=10,
            base_hp=10,
            default_count=0,
        )
        for key in ("bank_low", "bank_high", "bank_other")
    }
    return manor, templates


@pytest.fixture(params=["mission", "raid"])
def deduct(request):
    return mission_troops._deduct_troops_batch if request.param == "mission" else troop_ops._deduct_troops


def _counts(manor, model):
    return dict(model.objects.filter(manor=manor, count__gt=0).values_list("troop_template__key", "count"))


@pytest.mark.django_db
@pytest.mark.parametrize("requested, expected_manor, expected_bank", [(3, 2, 10), (8, 0, 7), (15, 0, 0)])
def test_dispatch_uses_manor_before_bank(inventory, deduct, requested, expected_manor, expected_bank):
    manor, templates = inventory
    template = templates["bank_high"]
    player = PlayerTroop.objects.create(manor=manor, troop_template=template, count=5)
    bank = TroopBankStorage.objects.create(manor=manor, troop_template=template, count=10)
    with transaction.atomic():
        deduct(Manor.objects.select_for_update().get(pk=manor.pk), {template.key: requested})
    player.refresh_from_db()
    bank.refresh_from_db()
    assert (player.count, bank.count) == (expected_manor, expected_bank)


@pytest.mark.django_db
def test_dispatch_supports_bank_only_type_and_ignores_nonpositive_entries(inventory, deduct):
    manor, templates = inventory
    bank = TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_high"], count=12)
    with transaction.atomic():
        deduct(
            Manor.objects.select_for_update().get(pk=manor.pk),
            {"bank_high": 7, "missing_zero": 0, "missing_negative": -4},
        )
    bank.refresh_from_db()
    assert bank.count == 5
    assert not PlayerTroop.objects.filter(manor=manor, troop_template=bank.troop_template).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_entry", [{"bank_other": 1}, {"bank_low": 10}])
def test_invalid_dispatch_does_not_partially_deduct_or_copy_troops(inventory, deduct, invalid_entry):
    manor, templates = inventory
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_high"], count=5)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_high"], count=5)
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_low"], count=2)
    before = (_counts(manor, PlayerTroop), _counts(manor, TroopBankStorage))
    with transaction.atomic():
        locked = Manor.objects.select_for_update().get(pk=manor.pk)
        # 在同一事务内捕获异常，证明服务先校验全部类型，未依赖外层回滚。
        with pytest.raises((TroopLoadoutError, RaidStartError)):
            deduct(locked, {"bank_high": 8, **invalid_entry})
    assert (_counts(manor, PlayerTroop), _counts(manor, TroopBankStorage)) == before


@pytest.mark.django_db
def test_query_includes_bank_only_types_only_when_requested(inventory):
    manor, templates = inventory
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_high"], count=5)
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_low"], count=0)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_high"], count=7)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_low"], count=9)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_other"], count=0)
    default_rows = get_player_troops(manor)
    assert [(row["key"], row["count"]) for row in default_rows] == [("bank_high", 5)]
    assert "bank_count" not in default_rows[0]
    assert "manor_count" not in default_rows[0]
    total_rows = {row["key"]: row for row in get_player_troops(manor, include_bank=True)}
    assert set(total_rows) == {"bank_high", "bank_low"}
    assert (total_rows["bank_high"]["count"], total_rows["bank_high"]["manor_count"]) == (12, 5)
    assert (total_rows["bank_low"]["count"], total_rows["bank_low"]["manor_count"]) == (9, 0)
    assert total_rows["bank_high"]["bank_count"] == 7
    assert total_rows["bank_low"]["bank_count"] == 9


@pytest.mark.django_db
def test_return_respects_recruitment_reservation_and_discards_lowest_tier(inventory, monkeypatch):
    manor, templates = inventory
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_other"], count=4990)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_other"], count=4995)
    TroopRecruitment.objects.create(
        manor=manor,
        troop_key="bank_other",
        troop_name="预留护院",
        quantity=8,
        equipment_costs={},
        retainer_cost=8,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )
    monkeypatch.setattr(
        "gameplay.services.recruitment.templates.get_recruit_config",
        lambda key: {"tech_level": 10 if key == "bank_high" else 1},
    )
    with transaction.atomic():
        result = troop_bank.return_troops_to_manor_or_bank_locked(
            Manor.objects.select_for_update().get(pk=manor.pk),
            {"bank_low": 6, "bank_high": 6},
            add_to_manor=lambda target, troops: mission_troops._add_troops_batch(
                target, troops, enforce_capacity=False
            ),
        )
    assert result == {"manor": {"bank_high": 2}, "bank": {"bank_high": 4, "bank_low": 1}, "discarded": {"bank_low": 5}}
    assert _counts(manor, PlayerTroop) == {"bank_other": 4990, "bank_high": 2}
    assert _counts(manor, TroopBankStorage) == {"bank_other": 4995, "bank_high": 4, "bank_low": 1}


@pytest.mark.parametrize("capacity", [1, 7])
def test_equal_tier_sampling_is_weighted_by_individual_count(monkeypatch, capacity):
    monkeypatch.setattr("gameplay.services.recruitment.templates.get_recruit_config", lambda _key: {"tech_level": 10})
    calls = []

    def weighted_sample(population, k, *, counts):
        calls.append((population, k, counts))
        return ["bank_high"] * k

    monkeypatch.setattr(troop_bank, "sample", weighted_sample)
    accepted, discarded = troop_bank._take_troops_up_to_capacity({"bank_low": 1, "bank_high": 99}, capacity)
    assert calls == [(["bank_low", "bank_high"], capacity, [1, 99])]
    assert accepted == {"bank_high": capacity}
    assert discarded == {"bank_low": 1, "bank_high": 99 - capacity}


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["mission", "raid"])
@pytest.mark.parametrize("has_report", [False, True])
def test_return_wrappers_restore_only_survivors_to_bank(inventory, monkeypatch, path, has_report):
    manor, templates = inventory
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_other"], count=5000)
    monkeypatch.setattr("battle.troops.load_troop_templates", lambda: {"bank_high": {}})
    report = (
        SimpleNamespace(losses={"attacker": {"casualties": [{"key": "bank_high", "lost": 3}]}}) if has_report else None
    )
    with transaction.atomic():
        locked = Manor.objects.select_for_update().get(pk=manor.pk)
        if path == "mission":
            mission_troops._return_surviving_troops_batch(locked, {"bank_high": 10}, report)
        else:
            troop_ops._return_surviving_troops(
                SimpleNamespace(attacker=locked, troop_loadout={"bank_high": 10}, battle_report=report)
            )
    assert _counts(manor, PlayerTroop) == {"bank_other": 5000}
    assert _counts(manor, TroopBankStorage) == {"bank_high": 7 if has_report else 10}


@pytest.mark.django_db
def test_failed_raid_release_settles_full_storage_once(inventory, django_user_model):
    manor, templates = inventory
    defender = ensure_manor(django_user_model.objects.create_user(username="dispatch_bank_defender"))
    PlayerTroop.objects.create(manor=manor, troop_template=templates["bank_other"], count=5000)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["bank_other"], count=4998)
    run = RaidRun.objects.create(attacker=manor, defender=defender, troop_loadout={"bank_high": 10})
    assert fail_raid_run_and_release_resources(run.pk, failure_reason=RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT)
    run.refresh_from_db()
    assert run.status == RaidRun.Status.FAILED
    assert run.resources_released
    assert _counts(manor, TroopBankStorage) == {"bank_other": 4998, "bank_high": 2}
    assert not fail_raid_run_and_release_resources(run.pk, failure_reason=RaidRun.FailureReason.INVALID_GUEST_SNAPSHOT)
    assert _counts(manor, TroopBankStorage) == {"bank_other": 4998, "bank_high": 2}
