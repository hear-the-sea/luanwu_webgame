from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEvent,
    ArenaEntry,
    ArenaFillServiceEpisode,
    ArenaFillServiceOutcome,
    ArenaTournament,
)
from gameplay.services.arena import virtual_fill_service as service
from tests.arena_services.test_virtual_backfill import _add_real_arena_entry as _add_real_arena_entry_with_stats
from tests.arena_services.test_virtual_backfill import _add_real_coop_entry

pytestmark = pytest.mark.django_db


def _add_real_arena_entry(event, username):
    return _add_real_arena_entry_with_stats(event, username, attack=200, defense=200, max_hp=2000)


def _opening():
    now = timezone.now()
    event = ArenaTournament.objects.create(player_limit=2, virtual_fill_at=now)
    _add_real_arena_entry(event, "fill_service_human")
    episode = service.observe_fill_service(mode="tournament", event_id=event.pk, now=now)
    return event, episode, now


def test_repeated_observation_is_one_episode_and_postfill_is_actual_inventory():
    event, episode, now = _opening()
    first_real_entry_at = event.entries.filter(source="player").values_list("joined_at", flat=True).first()
    assert episode.first_real_entry_at == first_real_entry_at
    assert episode.first_demand_at == first_real_entry_at
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=now)
    assert ArenaFillServiceEpisode.objects.count() == 1
    entry = _add_real_arena_entry(event, "fill_service_virtual")
    ArenaEntry.objects.filter(pk=entry.pk).update(source="virtual")
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=1), phase="postfill")
    episode.refresh_from_db()
    assert episode.slo_result == "virtual_filled"
    assert episode.last_snapshot["missing_count"] == 0
    assert episode.last_snapshot["virtual_entry_count"] == 1
    assert episode.last_snapshot["ready_eligibility"] == "not_revalidated"
    assert service.publish_pending_fill_outcomes() == 2
    assert service.publish_pending_fill_outcomes() == 0
    assert ArenaFillServiceOutcome.objects.count() == 2


def test_bracketed_deadline_failure_survives_late_success():
    event, episode, now = _opening()
    service.observe_fill_service(
        mode="tournament", event_id=event.pk, now=episode.checkpoint_at - timedelta(seconds=40)
    )
    service.observe_fill_service(
        mode="tournament", event_id=event.pk, now=episode.checkpoint_at + timedelta(seconds=20)
    )
    episode.refresh_from_db()
    assert episode.slo_result == "failed"
    _add_real_arena_entry(event, "late_human")
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=episode.checkpoint_at + timedelta(minutes=2))
    episode.refresh_from_db()
    assert episode.slo_result == "failed"
    assert episode.completion_kind == "human_satisfied"
    service.publish_pending_fill_outcomes()
    assert ArenaFillServiceOutcome.objects.get(phase="slo").result == "failed"
    assert ArenaFillServiceOutcome.objects.get(phase="completion").result == "human_satisfied"


def test_late_worker_does_not_invent_checkpoint_inventory():
    event, episode, now = _opening()
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=episode.checkpoint_at + timedelta(minutes=4))
    episode.refresh_from_db()
    assert episode.slo_result == "unknown"
    assert episode.unknown_observation_seconds > 0


def test_cancelled_before_deadline_is_not_failed_or_successful():
    event, episode, now = _opening()
    ArenaEntry.objects.filter(tournament=event).delete()
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=1))
    episode.refresh_from_db()
    assert episode.slo_result == "cancelled"
    with CaptureQueriesContext(connection) as queries:
        result = service.summarize_fill_service(since=now - timedelta(minutes=1), now=now)
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
    assert result["eligible_count"] == 0 and result["success_rate"] is None
    assert result["results"]["cancelled"] == 1


def test_coop_reopening_same_event_is_new_independent_episode():
    now = timezone.now()
    event = ArenaCoopEvent.objects.create(player_limit=2, virtual_fill_at=now)
    _add_real_coop_entry(event, "reopening_human")
    service.observe_fill_service(mode="coop", event_id=event.pk, now=now)
    event.virtual_fill_at += timedelta(minutes=30)
    event.save(update_fields=["virtual_fill_at"])
    service.observe_fill_service(mode="coop", event_id=event.pk, now=now + timedelta(minutes=5))
    assert ArenaFillServiceEpisode.objects.count() == 2
    service.collect_fill_service_outcomes(now=now + timedelta(minutes=6))
    old = ArenaFillServiceEpisode.objects.order_by("pk").first()
    assert old.slo_result == "unknown" and old.closed_at is not None


def test_new_first_human_after_cancellation_keeps_old_result_and_deadline():
    event, episode, now = _opening()
    ArenaEntry.objects.filter(tournament=event).delete()
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=1))
    _add_real_arena_entry(event, "returned_human")
    new = service.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=2))
    episode.refresh_from_db()
    assert episode.slo_result == "cancelled"
    assert new.pk != episode.pk and new.opening_ordinal == 2
    assert new.virtual_fill_at == episode.virtual_fill_at
    assert new.slo_result == ""


def test_publisher_failure_keeps_durable_payload_for_retry(monkeypatch):
    event, episode, now = _opening()
    _add_real_arena_entry(event, "second_human")
    service.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=1))
    original = ArenaFillServiceOutcome.objects.get_or_create

    def fail_publish(*args, **kwargs):
        raise RuntimeError("publisher stopped")

    monkeypatch.setattr(ArenaFillServiceOutcome.objects, "get_or_create", fail_publish)
    with pytest.raises(RuntimeError):
        service.publish_pending_fill_outcomes()
    episode.refresh_from_db()
    assert episode.publication_pending and episode.pending_outcomes
    monkeypatch.setattr(ArenaFillServiceOutcome.objects, "get_or_create", original)
    assert service.publish_pending_fill_outcomes() == 2


def test_observation_rolls_back_with_fill_transaction():
    event, episode, now = _opening()
    with pytest.raises(RuntimeError), transaction.atomic():
        _add_real_arena_entry(event, "rolled_back_human")
        service.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=1))
        raise RuntimeError("fill failed")
    episode.refresh_from_db()
    assert episode.slo_result == ""
    assert service.publish_pending_fill_outcomes() == 0


def test_idle_and_small_samples_are_distinct():
    now = timezone.now()
    assert service.summarize_fill_service(since=now - timedelta(days=7))["sample_status"] == "idle"
    event, episode, now = _opening()
    result = service.summarize_fill_service(since=now - timedelta(days=7))
    assert result["sample_status"] == "insufficient_samples"
    assert result["results"]["pending"] == 1
    assert len(result["backlog"]) == 1


def test_discovery_is_bounded_and_includes_full_human_events():
    now = timezone.now()
    for index in range(3):
        event = ArenaCoopEvent.objects.create(
            status="preparing", player_limit=1, virtual_fill_at=now + timedelta(minutes=30)
        )
        _add_real_coop_entry(event, f"discovered_human_{index}")
    assert service.collect_fill_service_outcomes(now=now, limit=1)["observed"] == 1
    assert ArenaFillServiceEpisode.objects.count() == 1
    assert ArenaFillServiceEpisode.objects.get().slo_result == "human_satisfied"
