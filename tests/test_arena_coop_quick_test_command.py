from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from gameplay.models import ArenaCoopEntry, ArenaCoopEvent
from gameplay.services.arena.coop_core import register_arena_coop_entry
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor


@pytest.mark.django_db
def test_arena_coop_quick_test_command_fills_existing_recruiting_pool_to_preparing():
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=2,
        guest_limit_per_entry=3,
        prepare_duration_seconds=120,
        boss_name="张无忌",
    )
    user = User.objects.create_user(
        username="arena_coop_seeded", password="pass123", email="arena_coop_seeded@test.local"
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_coop_quick_existing_tpl")
    guests = [create_guest(manor, template, suffix) for suffix in ["A", "B", "C"]]
    result = register_arena_coop_entry(manor, [guest.id for guest in guests])
    assert result.event.id == event.id
    out = StringIO()

    call_command("arena_coop_quick_test", verbosity=0, stdout=out)

    event.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.PREPARING
    assert event.prepare_ends_at is not None
    assert ArenaCoopEntry.objects.filter(event=event).count() == 2
    assert "共斗快速测试完成" in out.getvalue()


@pytest.mark.django_db
def test_arena_coop_quick_test_command_creates_requested_players_when_no_pool_exists():
    out = StringIO()

    call_command("arena_coop_quick_test", players=2, verbosity=0, stdout=out)

    event = ArenaCoopEvent.objects.get()
    assert event.status == ArenaCoopEvent.Status.RECRUITING
    assert event.entries.count() == 2
    assert ArenaCoopEntry.objects.count() == 2
    assert "本次创建测试账号 2 个" in out.getvalue()
