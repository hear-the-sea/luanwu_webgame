import pytest

from core.exceptions import BuildingConcurrentUpgradeLimitError, TechnologyConcurrentUpgradeLimitError
from gameplay.services.manor.core import ensure_manor, start_upgrade
from gameplay.services.technology import upgrade_technology


@pytest.mark.django_db
def test_building_upgrade_concurrency_limit(django_user_model, monkeypatch):
    monkeypatch.setattr(
        "gameplay.services.manor.core.schedule_building_completion",
        lambda *args, **kwargs: None,
    )

    user = django_user_model.objects.create_user(username="limit_building", password="pass12345")
    manor = ensure_manor(user)
    manor.grain = 500000
    manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])

    buildings = list(manor.buildings.all()[:3])
    assert len(buildings) >= 3

    start_upgrade(buildings[0])
    start_upgrade(buildings[1])

    with pytest.raises(BuildingConcurrentUpgradeLimitError, match=r"同时最多只能升级 2 个建筑"):
        start_upgrade(buildings[2])


@pytest.mark.django_db
def test_technology_upgrade_concurrency_limit(django_user_model, monkeypatch):
    monkeypatch.setattr(
        "gameplay.services.technology.schedule_technology_completion",
        lambda *args, **kwargs: None,
    )

    user = django_user_model.objects.create_user(username="limit_tech", password="pass12345")
    manor = ensure_manor(user)
    manor.silver = 1_000_000
    manor.save(update_fields=["silver"])

    upgrade_technology(manor, "march_art")
    upgrade_technology(manor, "architecture")

    with pytest.raises(TechnologyConcurrentUpgradeLimitError, match=r"同时最多只能研究 2 项科技"):
        upgrade_technology(manor, "dao_attack")
