from __future__ import annotations

import pytest

from core.exceptions import ProductionStartError
from gameplay.services.buildings import ranch as ranch_service
from gameplay.services.buildings import smithy as smithy_service
from gameplay.services.buildings import stable as stable_service
from gameplay.services.buildings.ranch import start_livestock_production
from gameplay.services.buildings.smithy import start_smelting_production
from gameplay.services.buildings.stable import start_horse_production
from gameplay.services.manor.core import ensure_manor


def _create_manor(username: str, django_user_model):
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    manor.grain = 500000
    manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])
    return manor


@pytest.mark.django_db
def test_start_horse_production_rejects_invalid_type_with_explicit_error(django_user_model):
    manor = _create_manor("production_horse_invalid", django_user_model)

    with pytest.raises(ProductionStartError, match="无效的马匹类型"):
        start_horse_production(manor, "not_exists", 1)


@pytest.mark.django_db
def test_start_livestock_production_rejects_invalid_type_with_explicit_error(django_user_model):
    manor = _create_manor("production_livestock_invalid", django_user_model)

    with pytest.raises(ProductionStartError, match="无效的家畜类型"):
        start_livestock_production(manor, "not_exists", 1)


@pytest.mark.django_db
def test_start_smelting_production_rejects_invalid_type_with_explicit_error(django_user_model):
    manor = _create_manor("production_smelting_invalid", django_user_model)

    with pytest.raises(ProductionStartError, match="无效的制作类型"):
        start_smelting_production(manor, "not_exists", 1)


@pytest.mark.django_db
def test_start_horse_production_scales_duration_by_quantity(django_user_model, monkeypatch):
    manor = _create_manor("production_horse_batch_duration", django_user_model)
    monkeypatch.setattr(
        stable_service,
        "HORSE_CONFIG",
        {
            "war_horse": {
                "grain_cost": 10,
                "base_duration": 60,
                "required_horsemanship": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr("gameplay.services.buildings.stable.calculate_production_duration", lambda *_args: 30)
    monkeypatch.setattr("gameplay.services.buildings.stable._schedule_production_completion", lambda *_args: None)

    production = start_horse_production(manor, "war_horse", 3)

    assert production.actual_duration == 90


@pytest.mark.django_db
def test_start_livestock_production_scales_duration_by_quantity(django_user_model, monkeypatch):
    manor = _create_manor("production_livestock_batch_duration", django_user_model)
    monkeypatch.setattr(
        ranch_service,
        "LIVESTOCK_CONFIG",
        {
            "chicken": {
                "grain_cost": 10,
                "base_duration": 60,
                "required_animal_husbandry": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr("gameplay.services.buildings.ranch.calculate_livestock_duration", lambda *_args: 30)
    monkeypatch.setattr("gameplay.services.buildings.ranch._schedule_livestock_completion", lambda *_args: None)

    production = start_livestock_production(manor, "chicken", 3)

    assert production.actual_duration == 90


@pytest.mark.django_db
def test_start_smelting_production_allows_batch_medicine_by_smithy_level(django_user_model, monkeypatch):
    manor = _create_manor("production_smelting_medicine_batch", django_user_model)
    monkeypatch.setattr(
        smithy_service,
        "METAL_CONFIG",
        {
            "zhixuesan": {
                "cost_type": "silver",
                "cost_amount": 50,
                "base_duration": 60,
                "category": "medicine",
                "required_smithy": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("gameplay.services.buildings.smithy._schedule_smelting_completion", lambda *_args: None)

    production = start_smelting_production(manor, "zhixuesan", 2)

    assert production.quantity == 2
    assert production.cost_amount == 100


@pytest.mark.django_db
def test_start_smelting_production_scales_duration_by_quantity(django_user_model, monkeypatch):
    manor = _create_manor("production_smelting_batch_duration", django_user_model)
    monkeypatch.setattr(
        smithy_service,
        "METAL_CONFIG",
        {
            "zhixuesan": {
                "cost_type": "silver",
                "cost_amount": 50,
                "base_duration": 60,
                "category": "medicine",
                "required_smithy": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("gameplay.services.buildings.smithy._schedule_smelting_completion", lambda *_args: None)

    production = start_smelting_production(manor, "zhixuesan", 3)

    assert production.actual_duration == smithy_service.calculate_smelting_duration(60, manor) * 3


@pytest.mark.django_db
def test_start_horse_production_rejects_malformed_runtime_config(django_user_model, monkeypatch):
    manor = _create_manor("production_horse_bad_config", django_user_model)
    monkeypatch.setattr(
        stable_service,
        "HORSE_CONFIG",
        {
            "bad_horse": {
                "grain_cost": 10,
                "base_duration": 60,
                "required_horsemanship": "bad",
            }
        },
    )

    with pytest.raises(AssertionError, match="invalid stable production required_horsemanship"):
        start_horse_production(manor, "bad_horse", 1)


@pytest.mark.django_db
def test_start_livestock_production_rejects_malformed_runtime_config(django_user_model, monkeypatch):
    manor = _create_manor("production_livestock_bad_config", django_user_model)
    monkeypatch.setattr(
        ranch_service,
        "LIVESTOCK_CONFIG",
        {
            "bad_livestock": {
                "grain_cost": 10,
                "base_duration": 60,
                "required_animal_husbandry": "bad",
            }
        },
    )

    with pytest.raises(AssertionError, match="invalid ranch production required_animal_husbandry"):
        start_livestock_production(manor, "bad_livestock", 1)


@pytest.mark.django_db
def test_start_smelting_production_rejects_malformed_runtime_config(django_user_model, monkeypatch):
    manor = _create_manor("production_smelting_bad_config", django_user_model)
    monkeypatch.setattr(
        smithy_service,
        "METAL_CONFIG",
        {
            "bad_metal": {
                "cost_type": "silver",
                "cost_amount": 10,
                "base_duration": 60,
                "category": "metal",
                "required_smelting": "bad",
            }
        },
    )

    with pytest.raises(AssertionError, match="invalid smithy production required_smelting"):
        start_smelting_production(manor, "bad_metal", 1)
