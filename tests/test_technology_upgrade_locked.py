from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from django.db import transaction

import gameplay.services.technology as technology_service
from core.exceptions import (
    InsufficientResourceError,
    TechnologyConcurrentUpgradeLimitError,
    TechnologyMaxLevelError,
    TechnologyNotFoundError,
    TechnologyUpgradeInProgressError,
)
from gameplay.models import Manor, PlayerTechnology, ResourceEvent
from gameplay.services.manor.core import ensure_manor
from gameplay.services.technology import (
    TechnologyUpgradeQuoteStaleError,
    apply_technology_upgrade_locked,
    quote_technology_upgrade,
)


def _create_manor(django_user_model, *, username: str) -> Manor:
    user = django_user_model.objects.create_user(
        username=username,
        password="pass12345",
    )
    return ensure_manor(user)


def _set_economy(
    manor: Manor,
    *,
    silver: int,
    prestige: int = 0,
    prestige_silver_spent: int = 0,
) -> None:
    manor.silver = silver
    manor.prestige = prestige
    manor.prestige_silver_spent = prestige_silver_spent
    manor.save(update_fields=["silver", "prestige", "prestige_silver_spent"])


@pytest.mark.django_db
def test_technology_upgrade_quote_is_immutable_and_read_only(
    django_user_model,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_quote_read_only",
    )

    quote = quote_technology_upgrade(manor, "march_art")

    assert quote.manor_id == manor.id
    assert quote.technology_key == "march_art"
    assert quote.current_level == 0
    assert quote.target_level == 1
    assert quote.silver_cost == technology_service.calculate_upgrade_cost(
        "march_art",
        0,
    )
    assert quote.active_upgrade_count == 0
    assert quote.to_payload()["technology_name"] == quote.technology_name
    assert not PlayerTechnology.objects.filter(
        manor=manor,
        tech_key="march_art",
    ).exists()

    with pytest.raises(FrozenInstanceError):
        setattr(quote, "current_level", 9)


@pytest.mark.django_db
def test_apply_technology_upgrade_locked_completes_exactly_one_level_atomically(
    django_user_model,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_locked_success",
    )
    existing = PlayerTechnology.objects.create(
        manor=manor,
        tech_key="march_art",
        level=2,
        is_upgrading=False,
    )
    quote = quote_technology_upgrade(manor, "march_art")
    starting_silver = quote.silver_cost + 321
    starting_prestige = 3
    starting_prestige_spent = 950
    _set_economy(
        manor,
        silver=starting_silver,
        prestige=starting_prestige,
        prestige_silver_spent=starting_prestige_spent,
    )
    invalidated: list[int] = []
    monkeypatch.setattr(
        technology_service,
        "invalidate_home_stats_cache",
        invalidated.append,
    )
    monkeypatch.setattr(
        technology_service,
        "schedule_technology_completion",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("synchronous technology upgrade must not schedule Celery")
        ),
    )
    monkeypatch.setattr(
        technology_service._notifications,
        "notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("synchronous technology upgrade must not notify")
        ),
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            upgraded = apply_technology_upgrade_locked(
                locked_manor,
                quote,
                sync_production=False,
            )
            assert invalidated == []

    existing.refresh_from_db()
    manor.refresh_from_db()
    assert upgraded.pk == existing.pk
    assert existing.level == 3
    assert existing.is_upgrading is False
    assert existing.upgrade_complete_at is None
    assert manor.silver == starting_silver - quote.silver_cost
    assert manor.prestige_silver_spent == (starting_prestige_spent + quote.silver_cost)
    prestige_gained = manor.prestige_silver_spent // 1000 - starting_prestige_spent // 1000
    assert manor.prestige == starting_prestige + prestige_gained

    event = ResourceEvent.objects.get(
        manor=manor,
        reason=ResourceEvent.Reason.TECH_UPGRADE,
    )
    assert event.resource_type == "silver"
    assert event.delta == -quote.silver_cost
    assert event.note == f"升级{quote.technology_name}"

    cache_callbacks = [
        callback for callback in callbacks if getattr(callback, "__name__", "") == "_invalidate_after_commit"
    ]
    assert len(cache_callbacks) == 1
    cache_callbacks[0]()
    assert invalidated == [manor.id]


@pytest.mark.django_db
def test_apply_technology_upgrade_locked_creates_missing_level_zero_row(
    django_user_model,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_locked_missing_row",
    )
    quote = quote_technology_upgrade(manor, "architecture")
    _set_economy(manor, silver=quote.silver_cost)

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        upgraded = apply_technology_upgrade_locked(
            locked_manor,
            quote,
            sync_production=False,
        )

    assert upgraded.level == 1
    assert upgraded.is_upgrading is False
    assert upgraded.upgrade_complete_at is None
    assert (
        PlayerTechnology.objects.filter(
            manor=manor,
            tech_key="architecture",
            level=1,
            is_upgrading=False,
            upgrade_complete_at=None,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_technology_upgrade_quote_reuses_human_eligibility_rules(
    django_user_model,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_quote_eligibility",
    )

    with pytest.raises(TechnologyNotFoundError):
        quote_technology_upgrade(manor, "missing_technology")

    PlayerTechnology.objects.create(
        manor=manor,
        tech_key="march_art",
        level=0,
        is_upgrading=True,
    )
    with pytest.raises(TechnologyUpgradeInProgressError):
        quote_technology_upgrade(manor, "march_art")

    architecture_template = technology_service.get_technology_template("architecture")
    assert architecture_template is not None
    PlayerTechnology.objects.create(
        manor=manor,
        tech_key="architecture",
        level=int(architecture_template["max_level"]),
    )
    with pytest.raises(TechnologyMaxLevelError):
        quote_technology_upgrade(manor, "architecture")

    PlayerTechnology.objects.create(
        manor=manor,
        tech_key="horsemanship",
        level=0,
        is_upgrading=True,
    )
    with pytest.raises(TechnologyConcurrentUpgradeLimitError):
        quote_technology_upgrade(manor, "dao_attack")


@pytest.mark.django_db
def test_apply_technology_upgrade_locked_rejects_stale_quote_before_spending(
    django_user_model,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_locked_stale_quote",
    )
    technology = PlayerTechnology.objects.create(
        manor=manor,
        tech_key="march_art",
        level=1,
    )
    quote = quote_technology_upgrade(manor, "march_art")
    _set_economy(manor, silver=quote.silver_cost + 100)
    PlayerTechnology.objects.filter(pk=technology.pk).update(level=2)

    with pytest.raises(TechnologyUpgradeQuoteStaleError):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            apply_technology_upgrade_locked(
                locked_manor,
                quote,
                sync_production=False,
            )

    manor.refresh_from_db()
    technology.refresh_from_db()
    assert manor.silver == quote.silver_cost + 100
    assert technology.level == 2
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.TECH_UPGRADE,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_apply_technology_upgrade_locked_requires_atomic_block(
    django_user_model,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_locked_requires_atomic",
    )
    quote = quote_technology_upgrade(manor, "march_art")

    with pytest.raises(RuntimeError, match="transaction.atomic"):
        apply_technology_upgrade_locked(
            manor,
            quote,
            sync_production=False,
        )


@pytest.mark.django_db
def test_apply_technology_upgrade_locked_preserves_insufficient_resource_error(
    django_user_model,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_locked_insufficient",
    )
    quote = quote_technology_upgrade(manor, "march_art")
    _set_economy(manor, silver=quote.silver_cost - 1)

    with pytest.raises(InsufficientResourceError):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            apply_technology_upgrade_locked(
                locked_manor,
                quote,
                sync_production=False,
            )

    assert not PlayerTechnology.objects.filter(
        manor=manor,
        tech_key="march_art",
    ).exists()
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.TECH_UPGRADE,
    ).exists()


@pytest.mark.django_db
def test_apply_technology_upgrade_locked_rolls_back_all_state_on_failure(
    django_user_model,
    django_capture_on_commit_callbacks,
    monkeypatch,
) -> None:
    manor = _create_manor(
        django_user_model,
        username="technology_locked_rollback",
    )
    technology = PlayerTechnology.objects.create(
        manor=manor,
        tech_key="march_art",
        level=2,
    )
    quote = quote_technology_upgrade(manor, "march_art")
    starting_silver = quote.silver_cost + 77
    starting_prestige = 4
    starting_prestige_spent = 250
    _set_economy(
        manor,
        silver=starting_silver,
        prestige=starting_prestige,
        prestige_silver_spent=starting_prestige_spent,
    )
    original_save = PlayerTechnology.save

    def _fail_upgraded_save(self, *args, **kwargs):
        if self.pk == technology.pk and self.level == quote.target_level:
            raise RuntimeError("forced technology persistence failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(PlayerTechnology, "save", _fail_upgraded_save)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(
            RuntimeError,
            match="forced technology persistence failure",
        ):
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                apply_technology_upgrade_locked(
                    locked_manor,
                    quote,
                    sync_production=False,
                )

    manor.refresh_from_db()
    technology.refresh_from_db()
    assert callbacks == []
    assert technology.level == 2
    assert manor.silver == starting_silver
    assert manor.prestige == starting_prestige
    assert manor.prestige_silver_spent == starting_prestige_spent
    assert not ResourceEvent.objects.filter(
        manor=manor,
        reason=ResourceEvent.Reason.TECH_UPGRADE,
    ).exists()
