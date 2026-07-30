from __future__ import annotations

import threading

import pytest
from django.db import close_old_connections, connection, transaction

from gameplay.models import Manor, PlayerTechnology, ResourceEvent
from gameplay.services.manor.core import ensure_manor
from gameplay.services.technology import (
    TechnologyUpgradeQuoteStaleError,
    apply_technology_upgrade_locked,
    quote_technology_upgrade,
)

pytestmark = [pytest.mark.integration]


def _require_isolated_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("technology upgrade row-lock race requires MySQL")
    if str(connection.settings_dict["NAME"]) != "test_webgame":
        pytest.skip("technology upgrade row-lock race only runs on test_webgame")


@pytest.mark.django_db(transaction=True)
def test_same_frozen_technology_quote_can_commit_at_most_once(
    django_user_model,
) -> None:
    _require_isolated_mysql()
    user = django_user_model.objects.create_user(
        username="technology_locked_mysql_race",
        password="pass12345",
    )
    manor = ensure_manor(user)
    quote = quote_technology_upgrade(manor, "march_art")
    manor.silver = quote.silver_cost
    manor.prestige = 0
    manor.prestige_silver_spent = 0
    manor.save(update_fields=["silver", "prestige", "prestige_silver_spent"])
    start = threading.Barrier(2)
    result_guard = threading.Lock()
    successes: list[int] = []
    failures: list[BaseException] = []

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            with transaction.atomic():
                locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
                upgraded = apply_technology_upgrade_locked(
                    locked_manor,
                    quote,
                    sync_production=False,
                )
            with result_guard:
                successes.append(int(upgraded.level))
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                failures.append(exc)
        finally:
            close_old_connections()

    workers = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert all(not worker.is_alive() for worker in workers)
    assert successes == [1]
    assert len(failures) == 1
    assert isinstance(failures[0], TechnologyUpgradeQuoteStaleError)

    manor.refresh_from_db()
    technology = PlayerTechnology.objects.get(
        manor=manor,
        tech_key="march_art",
    )
    assert technology.level == 1
    assert technology.is_upgrading is False
    assert technology.upgrade_complete_at is None
    assert manor.silver == 0
    assert manor.prestige_silver_spent == quote.silver_cost
    assert manor.prestige == quote.silver_cost // 1000
    event = ResourceEvent.objects.get(
        manor=manor,
        reason=ResourceEvent.Reason.TECH_UPGRADE,
    )
    assert event.delta == -quote.silver_cost
