from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import close_old_connections, connection

from gameplay.models import BotProfile, JailPrisoner
from gameplay.services.jail import VirtualJailCleanupResult, cleanup_virtual_player_jail
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestTemplate

pytestmark = [pytest.mark.integration]

CUTOFF = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)


def _require_isolated_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("virtual jail cleanup concurrency evidence requires MySQL")
    if str(connection.settings_dict["NAME"]) != "test_webgame":
        pytest.skip("virtual jail cleanup concurrency evidence only runs on test_webgame")
    assert connection.features.has_select_for_update_skip_locked is True


def _create_profile(django_user_model, *, username: str) -> BotProfile:
    manor = ensure_manor(django_user_model.objects.create_user(username=username))
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=314_159,
        next_growth_at=CUTOFF + timedelta(hours=1),
        abandon_at=CUTOFF + timedelta(days=30),
        retire_at=CUTOFF + timedelta(days=60),
    )


@pytest.mark.django_db(transaction=True)
def test_overlapping_cleanup_workers_release_each_prisoner_at_most_once(
    django_user_model,
) -> None:
    _require_isolated_mysql()
    profile = _create_profile(
        django_user_model,
        username="virtual_jail_concurrent_bot",
    )
    original = ensure_manor(django_user_model.objects.create_user(username="virtual_jail_concurrent_original"))
    template = GuestTemplate.objects.create(
        key="virtual_jail_concurrent_template",
        name="Concurrent jail cleanup prisoner",
        rarity="green",
        archetype="military",
        base_attack=100,
        base_intellect=80,
    )
    eligible_ids: list[int] = []
    for index in range(24):
        prisoner = JailPrisoner.objects.create(
            captor=profile.manor,
            original_manor=original,
            guest_template=template,
            original_guest_name=f"eligible-{index}",
            original_level=10,
            loyalty=50,
            captured_loyalty=50,
        )
        JailPrisoner.objects.filter(pk=prisoner.pk).update(captured_at=CUTOFF - timedelta(minutes=24 - index))
        eligible_ids.append(prisoner.id)
    post_cutoff = JailPrisoner.objects.create(
        captor=profile.manor,
        original_manor=original,
        guest_template=template,
        original_guest_name="post-cutoff",
        original_level=10,
        loyalty=50,
        captured_loyalty=50,
    )
    JailPrisoner.objects.filter(pk=post_cutoff.pk).update(captured_at=CUTOFF + timedelta(microseconds=1))

    start = threading.Barrier(2)
    results: list[VirtualJailCleanupResult] = []
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = cleanup_virtual_player_jail(
                cutoff=CUTOFF,
                batch_size=2,
                max_batches=20,
            )
            with result_guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    workers = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert len(results) == 2
    assert sum(result.released for result in results) == len(eligible_ids)
    assert sum(result.failed for result in results) == 0
    assert not JailPrisoner.objects.filter(
        id__in=eligible_ids,
        status=JailPrisoner.Status.HELD,
    ).exists()
    assert JailPrisoner.objects.filter(
        id__in=eligible_ids,
        status=JailPrisoner.Status.RELEASED,
    ).count() == len(eligible_ids)
    post_cutoff.refresh_from_db()
    assert post_cutoff.status == JailPrisoner.Status.HELD
