from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.utils import timezone

from gameplay.models import (
    ArenaFillServiceEpisode,
    ArenaFillServiceOutcome,
    BotMaintenanceAttempt,
    BotMaintenanceCycle,
    BotProfile,
    Message,
    ResourceEvent,
    ResourceType,
)
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.cache import CacheKeys


def _make_old(message: Message) -> None:
    Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(days=30))


@pytest.mark.django_db
def test_cleanup_old_data_command_reports_all_protected_rows_and_keeps_batch_semantics(django_user_model):
    user = django_user_model.objects.create_user(username="cleanup_command_user", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected reward",
        attachments={"items": {"reward_item": 1}},
    )
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claimed reward",
        attachments={"items": {"reward_item": 1}},
        is_claimed=True,
    )
    metadata_only = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="metadata only",
        attachments={"metadata": {"source": "legacy"}},
    )
    for message in (protected, claimed, metadata_only):
        _make_old(message)
    stdout = StringIO()
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)

    try:
        call_command(
            "cleanup_old_data",
            model="gameplay.Message",
            days=7,
            batch_size=1,
            stdout=stdout,
        )

        assert Message.objects.filter(pk=protected.pk).exists() is True
        assert Message.objects.filter(pk__in=[claimed.pk, metadata_only.pk]).exists() is False
        assert cache.get(cache_key) is None
        output = stdout.getvalue()
        assert "已删除 2 条" in output
        assert "保留 1 条未领取附件消息" in output
        assert "清理完成：共删除 2 条记录" in output
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
def test_cleanup_old_data_command_dry_run_reports_all_protected_rows(django_user_model):
    user = django_user_model.objects.create_user(username="cleanup_command_dry_run_user", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected reward",
        attachments={"resources": {"silver": 10}},
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain message")
    _make_old(protected)
    _make_old(plain)
    stdout = StringIO()
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)

    try:
        call_command(
            "cleanup_old_data",
            model="gameplay.Message",
            days=7,
            dry_run=True,
            batch_size=1,
            stdout=stdout,
        )

        assert Message.objects.filter(pk__in=[protected.pk, plain.pk]).count() == 2
        assert cache.get(cache_key) == 999
        output = stdout.getvalue()
        assert "将删除 1 条" in output
        assert "保留 1 条未领取附件消息" in output
        assert "模拟完成：共将删除 1 条记录" in output
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
@pytest.mark.parametrize("target_model", ["gameplay.ArenaFillServiceEpisode", "gameplay.ArenaFillServiceOutcome"])
def test_cleanup_old_data_command_cleans_fill_service_outcomes_before_episodes(target_model):
    old_at = timezone.now() - timedelta(days=2)
    episode = ArenaFillServiceEpisode.objects.create(
        mode="coop",
        event_id=7001,
        virtual_fill_at=old_at,
        checkpoint_at=old_at,
        first_observed_at=old_at,
        last_observed_at=old_at,
        closed_at=old_at,
        terminal_reason="fulfilled",
        slo_result="human_satisfied",
        last_snapshot={},
        pending_outcomes={},
        publication_pending=False,
    )
    outcome = ArenaFillServiceOutcome.objects.create(
        episode=episode,
        phase="slo",
        result="human_satisfied",
        logical_at=old_at,
        observed_at=old_at,
        payload={"result": "human_satisfied"},
    )
    ArenaFillServiceOutcome.objects.filter(pk=outcome.pk).update(published_at=old_at)
    unpublished = ArenaFillServiceEpisode.objects.create(
        mode="coop",
        event_id=7002,
        virtual_fill_at=old_at,
        checkpoint_at=old_at,
        first_observed_at=old_at,
        last_observed_at=old_at,
        closed_at=old_at,
        terminal_reason="fulfilled",
        slo_result="human_satisfied",
        last_snapshot={},
        pending_outcomes={},
        publication_pending=False,
    )
    stdout = StringIO()

    call_command(
        "cleanup_old_data",
        model=target_model,
        days=1,
        batch_size=1,
        stdout=stdout,
    )

    assert not ArenaFillServiceEpisode.objects.filter(pk=episode.pk).exists()
    assert not ArenaFillServiceOutcome.objects.filter(pk=outcome.pk).exists()
    assert ArenaFillServiceEpisode.objects.filter(pk=unpublished.pk).exists()
    assert "期次 1 条、结果 1 条" in stdout.getvalue()


@pytest.mark.django_db
def test_cleanup_old_data_command_rejects_non_positive_batch_size():
    with pytest.raises(CommandError, match="batch-size must be at least 1"):
        call_command("cleanup_old_data", batch_size=0)


@pytest.mark.django_db
def test_cleanup_old_data_command_caps_retention_at_fifteen_days(django_user_model):
    user = django_user_model.objects.create_user(username="cleanup_retention_cap_user", password="pass123")
    manor = ensure_manor(user)
    twenty_days_old = ResourceEvent.objects.create(
        manor=manor,
        resource_type=ResourceType.SILVER,
        delta=1,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="older than policy cap",
    )
    ten_days_old = ResourceEvent.objects.create(
        manor=manor,
        resource_type=ResourceType.SILVER,
        delta=1,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="inside policy window",
    )
    now = timezone.now()
    ResourceEvent.objects.filter(pk=twenty_days_old.pk).update(created_at=now - timedelta(days=20))
    ResourceEvent.objects.filter(pk=ten_days_old.pk).update(created_at=now - timedelta(days=10))
    stdout = StringIO()

    call_command(
        "cleanup_old_data",
        model="gameplay.ResourceEvent",
        days=30,
        batch_size=1,
        stdout=stdout,
    )

    assert not ResourceEvent.objects.filter(pk=twenty_days_old.pk).exists()
    assert ResourceEvent.objects.filter(pk=ten_days_old.pk).exists()
    assert "15 天前的记录" in stdout.getvalue()


@pytest.mark.django_db
def test_cleanup_old_data_attempt_deletion_rechecks_uncertain_cycle_safety(django_user_model):
    user = django_user_model.objects.create_user(username="cleanup_attempt_user", password="pass123")
    manor = ensure_manor(user)
    old_at = timezone.now() - timedelta(days=30)
    profile = BotProfile.objects.create(
        manor=manor,
        prestige_band="test",
        growth_seed=1,
        next_growth_at=old_at,
        abandon_at=old_at,
        retire_at=old_at,
    )

    def make_cycle(cycle_id: str, ordinal: int) -> BotMaintenanceCycle:
        return BotMaintenanceCycle.objects.create(
            cycle_id=cycle_id,
            interval_seed=cycle_id,
            profile=profile,
            cycle_ordinal=ordinal,
            trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
            max_actions=1,
            action_ordinal=1,
            status=BotMaintenanceCycle.Status.COMPLETED,
            started_at=old_at,
            completed_at=old_at,
        )

    def make_attempt(operation_id: str, cycle: BotMaintenanceCycle, outcome: str) -> BotMaintenanceAttempt:
        attempt = BotMaintenanceAttempt.objects.create(
            operation_id=operation_id,
            profile=profile,
            cycle=cycle,
            trigger="scheduled",
            attempt_ordinal=1,
            outcome=outcome,
            started_at=old_at,
        )
        BotMaintenanceAttempt.objects.filter(pk=attempt.pk).update(completed_at=old_at)
        return attempt

    safe_attempt = make_attempt(
        "cleanup-attempt-safe",
        make_cycle("cleanup-attempt-safe-cycle", 1),
        BotMaintenanceAttempt.Outcome.APPLIED,
    )
    uncertain_cycle = make_cycle("cleanup-attempt-uncertain-cycle", 2)
    protected_attempt = make_attempt(
        "cleanup-attempt-protected",
        uncertain_cycle,
        BotMaintenanceAttempt.Outcome.APPLIED,
    )
    uncertain_attempt = make_attempt(
        "cleanup-attempt-uncertain",
        uncertain_cycle,
        BotMaintenanceAttempt.Outcome.COMMIT_UNCERTAIN,
    )
    stdout = StringIO()

    call_command(
        "cleanup_old_data",
        model="gameplay.BotMaintenanceAttempt",
        days=15,
        batch_size=1,
        stdout=stdout,
    )

    assert not BotMaintenanceAttempt.objects.filter(pk=safe_attempt.pk).exists()
    assert BotMaintenanceAttempt.objects.filter(pk=protected_attempt.pk).exists()
    assert BotMaintenanceAttempt.objects.filter(pk=uncertain_attempt.pk).exists()
    assert "已删除 1 条" in stdout.getvalue()


@pytest.mark.parametrize(
    "command,options",
    [("cleanup_old_data", {}), ("cleanup_old_data", {"dry_run": True}), ("cleanup_data_report", {})],
)
def test_cleanup_commands_reject_negative_days_before_database_access(command, options):
    with pytest.raises(CommandError, match="days must be non-negative"):
        call_command(command, days=-1, **options)
