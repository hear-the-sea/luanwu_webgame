"""SQL fencing for virtual-only automatic timers inside guest-owned transactions."""

from datetime import datetime

from django.db import DatabaseError, transaction
from django.db.models import BooleanField, Value

from gameplay.services.runtime_configs import (
    RuntimeRoutingError,
    read_virtual_player_routing,
    runtime_routing_guard_expression,
)

from .runtime_assessment import assess_virtual_player_runtime
from .safety_preflight import check_v2_development_write_preflight


def virtual_auto_training_write_guard(*, now: datetime):
    """Never take routing FOR UPDATE after guest/profile locks.

    The caller applies this expression in its timer UPDATE, not as an earlier
    boolean permission. Already-authorized completion is outside this gate.
    """
    try:
        with transaction.atomic():
            routing = read_virtual_player_routing()
            if (
                routing.persisted
                and assess_virtual_player_runtime(routing).training_admission_allowed
                and check_v2_development_write_preflight(now=now).allowed
            ):
                return runtime_routing_guard_expression(routing)
    except (DatabaseError, RuntimeRoutingError, TypeError, ValueError):
        pass
    return Value(False, output_field=BooleanField())
