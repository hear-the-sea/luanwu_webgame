"""Activity service telemetry. Writers use event -> demand -> episode lock order.

Outcomes are frozen in the fill transaction, then copied to an immutable read
model by a bounded publisher. No broker callback is required for durability and
late publication never injects backdated events into finalized safety windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Exists, Min, OuterRef, Q
from django.utils import timezone

from gameplay.models import ArenaCoopEvent, ArenaTournament, ArenaVirtualDemand
from gameplay.models.arena_fill_service import ArenaFillServiceEpisode, ArenaFillServiceOutcome

MAX_BATCH_SIZE = 200


def _limit(value):
    return min(MAX_BATCH_SIZE, max(0, int(value)))


def _event_model(mode):
    if mode == "tournament":
        return ArenaTournament
    if mode == "coop":
        return ArenaCoopEvent
    raise ValueError("mode must be tournament or coop")


def _snapshot(event, mode, demand, phase):
    entries = event.entries.all()
    if event.status == "recruiting":
        entries = entries.filter(status="registered")
    elif mode == "coop":
        entries = entries.exclude(status="cancelled")
    counts = entries.aggregate(
        real=Count("pk", filter=Q(source="player")),
        virtual=Count("pk", filter=Q(source="virtual")),
        first_real=Min("joined_at", filter=Q(source="player")),
    )
    ready = training = 0
    if demand is not None:
        reserves = dict(demand.reserve_members.values("state").annotate(n=Count("pk")).values_list("state", "n"))
        ready, training = reserves.get("ready", 0), reserves.get("training", 0)
    population_band = "unknown"
    region = ""
    if counts["real"]:
        from gameplay.services.virtual_player_core.config import load_virtual_player_v2_config

        from .virtual_reserve_references import median_entry

        reference = median_entry(list(entries.filter(source="player").select_related("manor")))
        config = load_virtual_player_v2_config()
        if config is not None:
            population_band = config.band_for_prestige(int(reference.manor.prestige)).name
        region = str(reference.manor.region)
    return {
        "schema_version": 1,
        "phase": phase,
        "status": event.status,
        "capacity": int(event.player_limit),
        "real_entry_count": counts["real"],
        "virtual_entry_count": counts["virtual"],
        "missing_count": max(0, event.player_limit - counts["real"] - counts["virtual"]),
        # Stored membership counts are not a claim of revalidated lineup eligibility.
        "reserve_ready_count": ready,
        "reserve_training_count": training,
        "ready_eligibility": "not_revalidated",
        "first_real_entry_at": counts["first_real"].isoformat() if counts["first_real"] else None,
        "demand_version": demand.version if demand else None,
        "supply_band": demand.arena_supply_prestige_band if demand else "",
        "prestige_band": population_band,
        "region": region,
        "strength_segment": demand.arena_strength_segment if demand else "",
        "policy_version": demand.arena_training_policy_version if demand else None,
        "failure_reason": demand.last_failure_reason if demand else "",
    }


def _queue_outcome(episode, *, phase, result, now, snapshot, source):
    logical_at = episode.checkpoint_at if phase == "slo" else now
    payload = {
        "schema_version": 1,
        "episode_id": episode.pk,
        "event_id": episode.event_id,
        "mode": episode.mode,
        "phase": phase,
        "result": result,
        "logical_at": logical_at.isoformat(),
        "observed_at": now.isoformat(),
        "source": source,
        "virtual_fill_at": episode.virtual_fill_at.isoformat(),
        "checkpoint_at": episode.checkpoint_at.isoformat(),
        "collection_delay_seconds": max(0, (now - logical_at).total_seconds()),
        "extra_wait_seconds": max(0, (now - episode.virtual_fill_at).total_seconds()),
        "snapshot": dict(snapshot),
    }
    episode.pending_outcomes = {**episode.pending_outcomes, phase: payload}
    episode.publication_pending = True


def _set_slo(episode, result, now, snapshot, source):
    if episode.slo_result:
        return
    episode.slo_result = result
    episode.slo_observed_at = now
    _queue_outcome(episode, phase="slo", result=result, now=now, snapshot=snapshot, source=source)


def observe_fill_service_locked(*, event, mode, now=None, phase="observation"):
    """Capture actual state with the caller's event lock held, in its transaction.

    Integration hooks should call this before/after registration transitions and
    after cancellation. ``now`` must be the observation clock, not a due time.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("fill observation requires the owning event transaction")
    if not isinstance(event, _event_model(mode)):
        raise ValueError("event does not match mode")
    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        raise ValueError("observation time must be timezone-aware")
    if event.virtual_fill_at is None:
        return None
    demand_filter = {"tournament_id" if mode == "tournament" else "coop_event_id": event.pk}
    demand = ArenaVirtualDemand.objects.select_for_update().filter(**demand_filter).first()
    snapshot = _snapshot(event, mode, demand, phase)
    first_real_entry_at = (
        datetime.fromisoformat(snapshot["first_real_entry_at"]) if snapshot["first_real_entry_at"] else None
    )
    identity = dict(mode=mode, event_id=event.pk, virtual_fill_at=event.virtual_fill_at)
    episode = (
        ArenaFillServiceEpisode.objects.select_for_update().filter(**identity).order_by("-opening_ordinal").first()
    )
    opening_ordinal = 1
    if (
        episode is not None
        and episode.terminal_reason == "cancelled"
        and snapshot["real_entry_count"]
        and event.status == "recruiting"
    ):
        # Ordinary arenas can accept a new first human after everybody leaves
        # without changing their frozen deadline. Preserve the cancelled sample.
        opening_ordinal = episode.opening_ordinal + 1
        episode = None
    if episode is None:
        if not snapshot["real_entry_count"] or event.status == "cancelled":
            return None
        grace = max(0, int(getattr(settings, "ARENA_FILL_SERVICE_GRACE_SECONDS", 15 * 60)))
        episode = ArenaFillServiceEpisode.objects.create(
            **identity,
            opening_ordinal=opening_ordinal,
            demand=demand,
            checkpoint_at=event.virtual_fill_at + timedelta(seconds=grace),
            first_observed_at=current_time,
            last_observed_at=current_time,
            # Keep the model field typed as datetime; only the JSON snapshot
            # serializes this value for durable payload comparisons.
            first_real_entry_at=first_real_entry_at,
            # Demand formation is anchored to the source entry timestamp, not
            # to a delayed reconciliation/observation task.
            first_demand_at=first_real_entry_at if snapshot["missing_count"] else None,
            initial_missing_count=snapshot["missing_count"],
        )
    if episode.closed_at is not None or current_time < episode.last_observed_at:
        return episode
    if episode.last_snapshot == snapshot and current_time == episode.last_observed_at:
        return episode

    previous = episode.last_snapshot
    previous_observed_at = episode.last_observed_at
    interval = max(0, (current_time - max(episode.last_observed_at, episode.virtual_fill_at)).total_seconds())
    max_gap = max(0, int(getattr(settings, "ARENA_FILL_SERVICE_MAX_SAMPLE_GAP_SECONDS", 120)))
    if previous and previous.get("status") == "recruiting" and previous.get("real_entry_count", 0):
        if interval <= max_gap:
            episode.sampled_gap_seconds += interval * previous.get("missing_count", 0)
        else:
            episode.unknown_observation_seconds += interval
    elif not previous:
        episode.unknown_observation_seconds += max(0, (current_time - episode.virtual_fill_at).total_seconds())

    episode.demand = demand
    episode.last_snapshot = snapshot
    episode.last_observed_at = current_time
    episode.version += 1
    if episode.first_demand_at is None and snapshot["missing_count"] and snapshot["real_entry_count"]:
        # Do not fabricate a demand timestamp when the source entry has no
        # usable timestamp; the observation itself is not the formation time.
        episode.first_demand_at = first_real_entry_at
    cancelled = event.status == "cancelled" or (event.status == "recruiting" and not snapshot["real_entry_count"])
    full = snapshot["capacity"] > 0 and snapshot["real_entry_count"] > 0 and snapshot["missing_count"] == 0

    if cancelled:
        _set_slo(
            episode,
            "cancelled" if current_time <= episode.checkpoint_at else "unknown",
            current_time,
            snapshot,
            "observed_cancellation",
        )
        episode.closed_at, episode.terminal_reason = current_time, "cancelled"
    elif full:
        kind = "virtual_filled" if snapshot["virtual_entry_count"] else "human_satisfied"
        # A late current snapshot is not evidence of inventory at the checkpoint.
        _set_slo(
            episode,
            kind if current_time <= episode.checkpoint_at else "unknown",
            current_time,
            snapshot,
            "observed_full",
        )
        if episode.first_completed_at is None:
            episode.first_completed_at, episode.completion_kind = current_time, kind
            _queue_outcome(
                episode, phase="completion", result=kind, now=current_time, snapshot=snapshot, source="observed_full"
            )
        episode.closed_at, episode.terminal_reason = current_time, "fulfilled"
    else:
        if current_time >= episode.checkpoint_at:
            # A healthy minute sweep brackets the deadline; it need not execute
            # at the exact microsecond. A recruiting opening cannot have been
            # full without leaving recruiting; reopen changes virtual_fill_at.
            bracketed = (
                previous_observed_at <= episode.checkpoint_at
                and current_time - previous_observed_at <= timedelta(seconds=max_gap)
                and previous.get("status") == "recruiting"
                and previous.get("real_entry_count", 0) > 0
                and previous.get("missing_count", 0) > 0
                and snapshot["real_entry_count"] > 0
            )
            result = (
                "failed"
                if event.status == "recruiting" and (current_time == episode.checkpoint_at or bracketed)
                else "unknown"
            )
            _set_slo(
                episode,
                result,
                current_time,
                snapshot,
                "bracketed_checkpoint" if bracketed else "checkpoint_observation",
            )
        if event.status != "recruiting":
            _set_slo(episode, "unknown", current_time, snapshot, "terminal_without_evidence")
            episode.closed_at, episode.terminal_reason = current_time, "terminal_without_evidence"
    episode.save()
    return episode


@transaction.atomic
def observe_fill_service(*, mode, event_id, now=None, phase="observation"):
    event = _event_model(mode).objects.select_for_update().filter(pk=event_id).first()
    if event is None:
        return None
    return observe_fill_service_locked(event=event, mode=mode, now=now, phase=phase)


def _close_unobservable_episode(episode_id, now):
    """Only called after reading/locking the activity; never infer old cutoff state."""
    # Collection and retention discover rows independently. If retention won
    # the race, the missing episode is already safely handled.
    episode = ArenaFillServiceEpisode.objects.select_for_update().filter(pk=episode_id).first()
    if episode is None:
        return
    if episode.closed_at is not None:
        return
    _set_slo(episode, "unknown", now, episode.last_snapshot, "activity_missing_or_reopened")
    episode.closed_at, episode.terminal_reason = now, "missing_or_reopened"
    episode.last_observed_at = now
    episode.version += 1
    episode.save()


def publish_pending_fill_outcomes(*, limit=100):
    """Retryable, atomic publication into the dedicated immutable metric read model.

    External exporters can use outcome primary keys as their durable cursor. This
    sink tolerates arbitrary publication delay without altering logical timestamps.
    """
    ids = list(
        ArenaFillServiceEpisode.objects.filter(publication_pending=True)
        .order_by("pk")
        .values_list("pk", flat=True)[: _limit(limit)]
    )
    published = 0
    for episode_id in ids:
        with transaction.atomic():
            # The discovery query is only a bounded hint. Another publisher
            # may have completed (or retention may have removed) this episode
            # before we acquire its row lock; neither case needs a retry storm.
            episode = (
                ArenaFillServiceEpisode.objects.select_for_update()
                .filter(pk=episode_id, publication_pending=True)
                .first()
            )
            if episode is None:
                continue
            for phase, payload in episode.pending_outcomes.items():
                outcome, created = ArenaFillServiceOutcome.objects.get_or_create(
                    episode=episode,
                    phase=phase,
                    defaults=dict(
                        result=payload["result"],
                        logical_at=datetime.fromisoformat(payload["logical_at"]),
                        observed_at=datetime.fromisoformat(payload["observed_at"]),
                        payload=payload,
                    ),
                )
                if outcome.payload != payload:
                    raise ValueError("immutable arena fill outcome conflict")
                published += int(created)
            episode.pending_outcomes = {}
            episode.publication_pending = False
            episode.save(update_fields=["pending_outcomes", "publication_pending"])
    return published


def collect_fill_service_outcomes(*, now=None, limit=100):
    current_time = now or timezone.now()
    limit = _limit(limit)
    observed = 0
    # Oldest observed first, including failed/unknown episodes still awaiting fill.
    rows = list(
        ArenaFillServiceEpisode.objects.filter(closed_at__isnull=True)
        .order_by("last_observed_at", "pk")
        .values("pk", "mode", "event_id", "virtual_fill_at")[:limit]
    )
    for row in rows:
        with transaction.atomic():
            event = _event_model(row["mode"]).objects.select_for_update().filter(pk=row["event_id"]).first()
            if event is None or event.virtual_fill_at != row["virtual_fill_at"]:
                _close_unobservable_episode(row["pk"], current_time)
            else:
                observe_fill_service_locked(event=event, mode=row["mode"], now=current_time, phase="checkpoint")
            observed += 1
    # Include successful activities with no demand, and avoid endlessly resampling
    # the first completed events. Each mode has an independent bounded discovery.
    for mode in ("tournament", "coop"):
        known = ArenaFillServiceEpisode.objects.filter(
            mode=mode,
            event_id=OuterRef("pk"),
            virtual_fill_at=OuterRef("virtual_fill_at"),
        ).exclude(terminal_reason="cancelled")
        events = (
            _event_model(mode)
            .objects.filter(virtual_fill_at__isnull=False)
            .exclude(status="cancelled")
            .filter(
                Q(entries__source="player", entries__status="registered")
                | (~Q(status="recruiting") & Q(entries__source="player"))
            )
            .annotate(known_episode=Exists(known))
            .filter(known_episode=False)
            .order_by("virtual_fill_at", "pk")
            .values_list("pk", flat=True)
            .distinct()[:limit]
        )
        for event_id in list(events):
            observe_fill_service(mode=mode, event_id=event_id, now=current_time, phase="discovery")
            observed += 1
    return {"observed": observed, "published": publish_pending_fill_outcomes(limit=limit)}


def summarize_fill_service(*, since, until=None, mode=None, prestige_band=None, limit=100, now=None):
    """SELECT-only bounded diagnostic sample; unknown/cancelled are not successes."""
    current_time = now or timezone.now()
    rows = ArenaFillServiceEpisode.objects.filter(first_observed_at__gte=since)
    if until is not None:
        rows = rows.filter(first_observed_at__lt=until)
    if mode is not None:
        _event_model(mode)
        rows = rows.filter(mode=mode)
    if prestige_band is not None:
        rows = rows.filter(last_snapshot__prestige_band=prestige_band)
    size = _limit(limit)
    episodes = list(rows.order_by("first_observed_at", "pk")[: size + 1])
    results = dict.fromkeys(("virtual_filled", "human_satisfied", "failed", "unknown", "cancelled", "pending"), 0)
    backlog = []
    for episode in episodes[:size]:
        results[episode.slo_result or "pending"] += 1
        if episode.closed_at is None:
            backlog.append(
                {
                    "episode_id": episode.pk,
                    "missing_count": episode.last_snapshot.get("missing_count"),
                    "extra_wait_seconds": max(0, (current_time - episode.virtual_fill_at).total_seconds()),
                    "last_observed_at": episode.last_observed_at.isoformat(),
                }
            )
    eligible = results["virtual_filled"] + results["human_satisfied"] + results["failed"]
    days = {
        timezone.localdate(e.slo_observed_at)
        for e in episodes[:size]
        if e.slo_result in {"virtual_filled", "human_satisfied", "failed"} and e.slo_observed_at
    }
    complete_sample = len(episodes) <= size
    status = (
        "idle"
        if not episodes
        else (
            "unknown"
            if results["unknown"]
            else "sufficient" if eligible >= 30 and len(days) >= 3 and complete_sample else "insufficient_samples"
        )
    )
    return {
        "results": results,
        "eligible_count": eligible,
        "sample_status": status,
        "observed_days": len(days),
        "success_rate": (results["virtual_filled"] + results["human_satisfied"]) / eligible if eligible else None,
        "sample_count": min(size, len(episodes)),
        "truncated": len(episodes) > size,
        "backlog": backlog,
    }
