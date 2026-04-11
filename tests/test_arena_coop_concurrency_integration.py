from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connection

from core.exceptions import ArenaEntryStateError
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent
from gameplay.services.arena.coop_core import register_arena_coop_entry
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_register_arena_coop_entry_concurrent_requests_allow_only_one_active_entry():
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    user = User.objects.create_user(
        username=f"arena_coop_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
        email=f"arena_coop_concurrent_{uuid.uuid4().hex[:8]}@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template(f"arena_coop_concurrency_tpl_{uuid.uuid4().hex[:8]}")
    guests = [create_guest(manor, template, suffix) for suffix in ["A", "B", "C"]]
    selected_guest_ids = [guest.id for guest in guests]

    barrier = threading.Barrier(2)
    successes: list[int] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            local_manor = type(manor).objects.get(pk=manor.pk)
            barrier.wait(timeout=5)
            result = register_arena_coop_entry(local_manor, selected_guest_ids)
            successes.append(result.entry.id)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    manor.refresh_from_db(fields=["arena_coop_participations_today", "arena_coop_participation_date"])
    active_entries = list(
        ArenaCoopEntry.objects.filter(manor=manor, status=ArenaCoopEntry.Status.REGISTERED)
        .select_related("event")
        .order_by("id")
    )

    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ArenaEntryStateError)
    assert "您已有进行中的围攻光明顶报名，请等待本场结束" in str(errors[0])
    assert len(active_entries) == 1
    assert active_entries[0].event.status == ArenaCoopEvent.Status.RECRUITING
    assert active_entries[0].entry_guests.count() == 3
    assert manor.arena_coop_participations_today == 1
    for guest in guests:
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.ARENA
