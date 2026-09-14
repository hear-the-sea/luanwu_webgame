import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from gameplay.models import ArenaFillServiceEpisode, ArenaFillServiceOutcome
from gameplay.services.arena.fill_service_retention import cleanup_fill_service_rows


@pytest.mark.django_db
@pytest.mark.parametrize("recent_phase", ["slo", "completion"])
def test_delayed_publication_protects_the_entire_episode_in_report_and_cleanup(recent_phase):
    now = timezone.now()
    cutoff = now - timedelta(days=90)
    old_at = cutoff - timedelta(days=1)
    episode = ArenaFillServiceEpisode.objects.create(
        mode="coop",
        event_id=7201,
        virtual_fill_at=old_at,
        checkpoint_at=old_at,
        first_observed_at=old_at,
        last_observed_at=old_at,
        closed_at=old_at,
        terminal_reason="fulfilled",
        slo_result="virtual_filled",
    )
    for phase in ("slo", "completion"):
        outcome = ArenaFillServiceOutcome.objects.create(
            episode=episode,
            phase=phase,
            result="virtual_filled",
            logical_at=old_at,
            observed_at=old_at,
            payload={"result": "virtual_filled"},
        )
        if phase != recent_phase:
            ArenaFillServiceOutcome.objects.filter(pk=outcome.pk).update(published_at=old_at)

    for model in ("gameplay.ArenaFillServiceEpisode", "gameplay.ArenaFillServiceOutcome"):
        report = StringIO()
        call_command("cleanup_data_report", model=model, days=90, json=True, stdout=report)
        assert json.loads(report.getvalue())["totals"]["stale_records"] == 0
        preview = StringIO()
        call_command("cleanup_old_data", model=model, days=90, dry_run=True, stdout=preview)
        assert "共将删除 0 条记录" in preview.getvalue()

    for dry_run in (True, False):
        assert cleanup_fill_service_rows(cutoff=cutoff, batch_size=1, dry_run=dry_run).deleted == 0
    assert ArenaFillServiceEpisode.objects.filter(pk=episode.pk).exists()
    assert ArenaFillServiceOutcome.objects.filter(episode=episode).count() == 2

    # Equality belongs to the retained side of the cutoff. An old SLO must not
    # let a fresh completion outcome be deleted through the parent episode.
    ArenaFillServiceOutcome.objects.filter(episode=episode, phase=recent_phase).update(published_at=cutoff)
    assert cleanup_fill_service_rows(cutoff=cutoff, batch_size=1).deleted == 0
    ArenaFillServiceOutcome.objects.filter(episode=episode, phase=recent_phase).update(published_at=old_at)
    result = cleanup_fill_service_rows(cutoff=cutoff, batch_size=1)
    assert (result.episodes, result.outcomes) == (1, 2)
    assert not ArenaFillServiceEpisode.objects.filter(pk=episode.pk).exists()
    assert not ArenaFillServiceOutcome.objects.filter(episode_id=episode.pk).exists()
