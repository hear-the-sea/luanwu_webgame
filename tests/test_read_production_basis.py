from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

import gameplay.services.resources as resource_service
from gameplay.models import ResourceType
from gameplay.services.manor.core import ensure_manor
from gameplay.services.resources import ResourceProductionBasis


@pytest.mark.django_db
@override_settings(RESOURCE_SYNC_MIN_INTERVAL_SECONDS=0)
def test_read_projection_prepares_basis_for_same_manor(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="read_projection_basis", password="test123")
    manor = ensure_manor(user)
    manor.resource_updated_at = timezone.now() - timedelta(hours=1)
    manor.warehouse_grain_quantity = manor.grain
    manor.save(update_fields=["resource_updated_at"])

    basis = ResourceProductionBasis(
        hourly_rates=((ResourceType.GRAIN, 120.0),),
        personnel_grain_cost_per_hour=4,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(resource_service, "load_resource_production_basis", lambda _manor: basis)

    def fake_build(_manor, **kwargs):
        captured.update(kwargs)
        return {}, {}, False

    monkeypatch.setattr(resource_service, "_build_production_snapshot", fake_build)

    prepared_basis = resource_service.project_resource_production_for_read(manor)

    assert captured["production_basis"] is basis
    assert prepared_basis is basis
