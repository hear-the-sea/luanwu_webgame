"""Shared retention rules for historical records and terminal work rows."""

from __future__ import annotations

from datetime import datetime

from django.apps import apps
from django.db import transaction
from django.db.models import Q, Subquery

from common.constants.retention import MAX_RECORD_RETENTION_DAYS


def bounded_retention_days(value: int | str, *, default: int = MAX_RECORD_RETENTION_DAYS) -> int:
    """Clamp configured retention to a positive value no greater than the policy cap."""

    try:
        days = int(value)
    except (TypeError, ValueError):
        days = int(default)
    return min(MAX_RECORD_RETENTION_DAYS, max(1, days))


TERMINAL_RECORD_TIME_FIELDS = {
    "guests.GuestRecruitment": "finished_at",
    "gameplay.MissionRun": "completed_at",
    "gameplay.BotMaintenanceCompletionEvent": "processed_at",
    "gameplay.BotMaintenanceCycle": "completed_at",
    "gameplay.BotMaintenanceAttempt": "completed_at",
}


def _get_model(model_path: str):
    app_label, model_name = model_path.split(".", 1)
    return apps.get_model(app_label, model_name)


def eligible_record_queryset(model_path: str, *, cutoff: datetime):
    """Return only expired rows that are historical and safe to discard.

    Operational rows that still represent pending work, active missions, or an
    uncertain virtual-player commit stay available for recovery.
    """

    model = _get_model(model_path)
    queryset = model.objects.all()

    if model_path == "guests.GuestRecruitment":
        return queryset.filter(
            status__in=("completed", "failed"),
            finished_at__lt=cutoff,
        )
    if model_path == "gameplay.MissionRun":
        return queryset.filter(status="completed", completed_at__lt=cutoff)
    if model_path == "gameplay.BotMaintenanceCompletionEvent":
        return queryset.filter(status="applied", processed_at__lt=cutoff)
    if model_path == "gameplay.BotMaintenanceCycle":
        return (
            queryset.filter(status="completed", completed_at__lt=cutoff)
            .exclude(attempts__outcome="commit_uncertain")
            .distinct()
        )
    if model_path == "gameplay.BotMaintenanceAttempt":
        uncertain_cycle_ids = (
            apps.get_model("gameplay", "BotMaintenanceCycle")
            .objects.filter(attempts__outcome="commit_uncertain")
            .values("pk")
        )
        return (
            queryset.filter(
                completed_at__lt=cutoff,
                outcome__in=("busy", "no_action", "applied", "programmer_error"),
            )
            .filter(Q(cycle__isnull=True) | Q(cycle__status="completed", cycle__completed_at__lt=cutoff))
            .filter(Q(cycle__isnull=True) | ~Q(cycle_id__in=Subquery(uncertain_cycle_ids)))
        )

    time_field = TERMINAL_RECORD_TIME_FIELDS.get(model_path)
    if time_field is not None:
        return queryset.filter(**{f"{time_field}__lt": cutoff})
    raise ValueError(f"unknown terminal retention model: {model_path}")


def delete_eligible_record_batch(model_path: str, *, cutoff: datetime, record_ids: list[int]) -> int:
    """Delete one previously selected batch after rechecking its retention safety.

    MySQL rejects deleting maintenance attempts through a queryset whose
    eligibility subquery reads the same table. Materialize eligible IDs under
    the normal cycle-to-attempt lock order, then delete by those IDs.
    """

    ids = tuple(dict.fromkeys(int(record_id) for record_id in record_ids))
    if not ids:
        return 0

    model = _get_model(model_path)
    database_alias = model.objects.db
    if model_path not in {"gameplay.BotMaintenanceAttempt", "gameplay.BotMaintenanceCycle"}:
        eligible = eligible_record_queryset(model_path, cutoff=cutoff).using(database_alias).filter(pk__in=ids)
        deleted, _ = eligible.delete()
        return int(deleted)

    with transaction.atomic(using=database_alias):
        if model_path == "gameplay.BotMaintenanceAttempt":
            cycle_model = apps.get_model("gameplay", "BotMaintenanceCycle")
            cycle_ids = sorted(
                set(
                    model.objects.using(database_alias)
                    .filter(pk__in=ids)
                    .exclude(cycle_id__isnull=True)
                    .values_list("cycle_id", flat=True)
                )
            )
            if cycle_ids:
                list(
                    cycle_model.objects.using(database_alias)
                    .select_for_update()
                    .filter(pk__in=cycle_ids)
                    .order_by("pk")
                    .values_list("pk", flat=True)
                )

        locked_rows = list(
            model.objects.using(database_alias)
            .select_for_update()
            .filter(pk__in=ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if not locked_rows:
            return 0

        eligible_ids = list(
            eligible_record_queryset(model_path, cutoff=cutoff)
            .using(database_alias)
            .filter(pk__in=locked_rows)
            .values_list("pk", flat=True)
        )
        if not eligible_ids:
            return 0

        deleted, _ = model.objects.using(database_alias).filter(pk__in=eligible_ids).delete()
        return int(deleted)


__all__ = [
    "MAX_RECORD_RETENTION_DAYS",
    "TERMINAL_RECORD_TIME_FIELDS",
    "bounded_retention_days",
    "delete_eligible_record_batch",
    "eligible_record_queryset",
]
