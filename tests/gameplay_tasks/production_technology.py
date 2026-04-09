from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import OperationalError
from django.utils import timezone

import gameplay.tasks as tasks
from core.exceptions.recruitment_extended import TroopRecruitmentNotReadyError, TroopTemplateNotFoundError
from tests.gameplay_tasks.support import Chain, patch_model


@pytest.mark.django_db
def test_complete_horse_production_reschedules(monkeypatch):
    now = timezone.now()
    production = SimpleNamespace(complete_at=now + timedelta(seconds=9))

    class _HorseProduction:
        class Status:
            PRODUCING = "producing"

        objects = Chain(first_result=production)

    monkeypatch.setattr("gameplay.models.HorseProduction", _HorseProduction)
    monkeypatch.setattr("gameplay.services.buildings.stable.finalize_horse_production", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tasks.timezone, "now", lambda: now)

    called = {}

    def _apply_async(*, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown

    monkeypatch.setattr(tasks.complete_horse_production, "apply_async", _apply_async)

    assert tasks.complete_horse_production.run(99) == "rescheduled"
    assert called["args"] == [99]
    assert called["countdown"] > 0


@pytest.mark.django_db
def test_complete_horse_production_reschedules_with_ceil_for_fractional_remaining(monkeypatch):
    now = timezone.now()
    production = SimpleNamespace(complete_at=now + timedelta(milliseconds=200))

    class _HorseProduction:
        class Status:
            PRODUCING = "producing"

        objects = Chain(first_result=production)

    monkeypatch.setattr("gameplay.models.HorseProduction", _HorseProduction)
    monkeypatch.setattr("gameplay.tasks.production.timezone.now", lambda: now)

    called = {}

    def _safe_apply_async_with_dedup(*_args, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown
        return True

    monkeypatch.setattr("gameplay.tasks.production.safe_apply_async_with_dedup", _safe_apply_async_with_dedup)

    assert tasks.complete_horse_production.run(103) == "rescheduled"
    assert called["args"] == [103]
    assert called["countdown"] == 1


@pytest.mark.django_db
def test_scan_troop_recruitments_counts(monkeypatch):
    recruitments = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

    class _TroopRecruitment:
        class Status:
            RECRUITING = "recruiting"

        objects = Chain(slice_result=recruitments)

    monkeypatch.setattr("gameplay.models.TroopRecruitment", _TroopRecruitment)

    finalized_ids = []

    def _finalize(recruitment, **_kwargs):
        finalized_ids.append(recruitment.id)
        if recruitment.id == 1:
            raise TroopRecruitmentNotReadyError(message="募兵尚未完成")
        return None

    monkeypatch.setattr("gameplay.services.recruitment.recruitment.finalize_troop_recruitment", _finalize)

    assert tasks.scan_troop_recruitments() == 1
    assert finalized_ids == [1, 2]


@pytest.mark.django_db
def test_complete_troop_recruitment_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()
    recruitment = SimpleNamespace(complete_at=now - timedelta(seconds=1), id=21)
    patch_model(monkeypatch, "gameplay.models.TroopRecruitment", first_result=recruitment)
    monkeypatch.setattr(
        "gameplay.tasks.recruitment.complete_troop_recruitment.retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.finalize_troop_recruitment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken troop recruitment finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken troop recruitment finalize contract"):
        tasks.complete_troop_recruitment.run(21)


@pytest.mark.django_db
def test_complete_troop_recruitment_skips_when_not_ready(monkeypatch):
    now = timezone.now()
    recruitment = SimpleNamespace(complete_at=now - timedelta(seconds=1), id=22)
    patch_model(monkeypatch, "gameplay.models.TroopRecruitment", first_result=recruitment)
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.finalize_troop_recruitment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TroopRecruitmentNotReadyError(message="募兵状态不正确，当前状态不是 recruiting")
        ),
    )

    assert tasks.complete_troop_recruitment.run(22) == "skipped"


@pytest.mark.django_db
def test_complete_troop_recruitment_skips_when_troop_template_missing(monkeypatch):
    now = timezone.now()
    recruitment = SimpleNamespace(complete_at=now - timedelta(seconds=1), id=23)
    patch_model(monkeypatch, "gameplay.models.TroopRecruitment", first_result=recruitment)
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.finalize_troop_recruitment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TroopTemplateNotFoundError("missing_template")),
    )

    assert tasks.complete_troop_recruitment.run(23) == "skipped"


@pytest.mark.django_db
def test_scan_troop_recruitments_programming_error_bubbles_up(monkeypatch):
    recruitments = [SimpleNamespace(id=1)]

    class _TroopRecruitment:
        class Status:
            RECRUITING = "recruiting"

        objects = Chain(slice_result=recruitments)

    monkeypatch.setattr("gameplay.models.TroopRecruitment", _TroopRecruitment)
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment.finalize_troop_recruitment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken troop recruitment scan contract")),
    )

    with pytest.raises(AssertionError, match="broken troop recruitment scan contract"):
        tasks.scan_troop_recruitments()


@pytest.mark.django_db
def test_complete_technology_upgrade_reschedules(monkeypatch):
    now = timezone.now()
    tech = SimpleNamespace(upgrade_complete_at=now + timedelta(seconds=10))

    patch_model(monkeypatch, "gameplay.models.PlayerTechnology", first_result=tech)
    monkeypatch.setattr(tasks.timezone, "now", lambda: now)
    monkeypatch.setattr(tasks, "finalize_technology_upgrade", lambda *_args, **_kwargs: True)

    called = {}

    def _apply_async(*, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown

    monkeypatch.setattr(tasks.complete_technology_upgrade, "apply_async", _apply_async)

    assert tasks.complete_technology_upgrade.run(5) == "rescheduled"
    assert called["args"] == [5]
    assert called["countdown"] > 0


@pytest.mark.django_db
def test_complete_technology_upgrade_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()
    tech = SimpleNamespace(upgrade_complete_at=now - timedelta(seconds=1), id=5)

    patch_model(monkeypatch, "gameplay.models.PlayerTechnology", first_result=tech)
    monkeypatch.setattr(
        "gameplay.tasks.technology.complete_technology_upgrade.retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "gameplay.tasks.technology.finalize_technology_upgrade",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken technology finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken technology finalize contract"):
        tasks.complete_technology_upgrade.run(5)


@pytest.mark.django_db
def test_scan_technology_upgrades_counts(monkeypatch):
    techs = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]

    class _Status:
        UPGRADING = "upgrading"

    patch_model(monkeypatch, "gameplay.models.PlayerTechnology", slice_result=techs, status_cls=_Status)

    def _finalize(tech, **_kwargs):
        return tech.id in {1, 3}

    monkeypatch.setattr("gameplay.tasks.technology.finalize_technology_upgrade", _finalize)

    assert tasks.scan_technology_upgrades() == 2


@pytest.mark.django_db
def test_scan_technology_upgrades_programming_error_bubbles_up(monkeypatch):
    techs = [SimpleNamespace(id=1)]

    class _Status:
        UPGRADING = "upgrading"

    patch_model(monkeypatch, "gameplay.models.PlayerTechnology", slice_result=techs, status_cls=_Status)
    monkeypatch.setattr(
        "gameplay.tasks.technology.finalize_technology_upgrade",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken technology scan contract")),
    )

    with pytest.raises(AssertionError, match="broken technology scan contract"):
        tasks.scan_technology_upgrades()


@pytest.mark.django_db
def test_complete_livestock_production_not_found(monkeypatch):
    patch_model(monkeypatch, "gameplay.models.LivestockProduction", first_result=None)
    monkeypatch.setattr(
        "gameplay.services.buildings.ranch.finalize_livestock_production", lambda *_args, **_kwargs: True
    )
    assert tasks.complete_livestock_production.run(1) == "not_found"


@pytest.mark.django_db
def test_complete_horse_production_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()
    production = SimpleNamespace(complete_at=now - timedelta(seconds=1), id=110)

    class _HorseProduction:
        class Status:
            PRODUCING = "producing"

        objects = Chain(first_result=production)

    monkeypatch.setattr("gameplay.models.HorseProduction", _HorseProduction)
    monkeypatch.setattr(
        "gameplay.tasks.production.complete_horse_production.retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "gameplay.services.buildings.stable.finalize_horse_production",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken horse finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken horse finalize contract"):
        tasks.complete_horse_production.run(110)


@pytest.mark.django_db
def test_complete_horse_production_retries_for_database_infrastructure_error(monkeypatch):
    now = timezone.now()
    production = SimpleNamespace(complete_at=now - timedelta(seconds=1), id=111)

    class _HorseProduction:
        class Status:
            PRODUCING = "producing"

        objects = Chain(first_result=production)

    monkeypatch.setattr("gameplay.models.HorseProduction", _HorseProduction)
    monkeypatch.setattr(
        "gameplay.services.buildings.stable.finalize_horse_production",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OperationalError("db down")),
    )

    retried = {}

    def _retry(*, exc=None, **_kwargs):
        retried["exc"] = exc
        raise RuntimeError("retry requested")

    monkeypatch.setattr("gameplay.tasks.production.complete_horse_production.retry", _retry)

    with pytest.raises(RuntimeError, match="retry requested"):
        tasks.complete_horse_production.run(111)

    assert isinstance(retried["exc"], OperationalError)


@pytest.mark.django_db
def test_scan_smelting_productions_counts(monkeypatch):
    productions = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

    class _Status:
        PRODUCING = "producing"

    patch_model(monkeypatch, "gameplay.models.SmeltingProduction", slice_result=productions, status_cls=_Status)
    monkeypatch.setattr(
        "gameplay.services.buildings.smithy.finalize_smelting_production",
        lambda production, **_kwargs: production.id == 2,
    )

    assert tasks.scan_smelting_productions() == 1


@pytest.mark.django_db
def test_scan_horse_productions_programming_error_bubbles_up(monkeypatch):
    productions = [SimpleNamespace(id=1)]

    class _Status:
        PRODUCING = "producing"

    patch_model(monkeypatch, "gameplay.models.HorseProduction", slice_result=productions, status_cls=_Status)
    monkeypatch.setattr(
        "gameplay.services.buildings.stable.finalize_horse_production",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken horse scan contract")),
    )

    with pytest.raises(AssertionError, match="broken horse scan contract"):
        tasks.scan_horse_productions()
