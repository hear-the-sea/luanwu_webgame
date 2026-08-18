from __future__ import annotations

from typing import Any

from django.db.models import Exists, OuterRef
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotExternalStrengthReconciliation,
)
from gameplay.services.virtual_player_core.config import VirtualPlayerConfigError, load_virtual_player_v2_config
from gameplay.services.virtual_player_core.growth_control import growth_control_reference_selection
from gameplay.services.virtual_player_core.policy_registry import PolicyRegistryError, get_policy_release
from gameplay.services.virtual_player_core.projection import (
    ProjectionRuleError,
    StrengthSummary,
    validate_strength_within_cap,
)
from gameplay.services.virtual_player_core.random_context import (
    RandomContext,
    UnsupportedRandomDomainError,
    UnsupportedRngVersionError,
)
from gameplay.services.virtual_player_core.reference_snapshots import (
    ReferenceSnapshotError,
    load_manor_strength_summary,
)

_HAS_UNRESOLVED_RECONCILIATION = "_arena_has_unresolved_reconciliation"
_HAS_APPLIED_RECONCILIATION = "_arena_has_applied_reconciliation"


class ArenaStrengthProtectionError(ValueError):
    pass


def with_arena_reconciliation_state(queryset):
    """Annotate BotProfile candidates and exclude every unresolved intent."""
    reconciliations = BotExternalStrengthReconciliation.objects.filter(profile_id=OuterRef("pk"))
    return queryset.annotate(
        **{
            _HAS_UNRESOLVED_RECONCILIATION: Exists(
                reconciliations.exclude(status=BotExternalStrengthReconciliation.Status.APPLIED)
            ),
            _HAS_APPLIED_RECONCILIATION: Exists(
                reconciliations.filter(status=BotExternalStrengthReconciliation.Status.APPLIED)
            ),
        }
    ).filter(**{_HAS_UNRESOLVED_RECONCILIATION: False})


def with_arena_reserve_guard(queryset):
    """Annotate virtual profiles with the reserve-membership guard."""
    return queryset.annotate(
        maintenance_has_arena_reserve=Exists(
            ArenaVirtualReserveMember.objects.filter(profile_id=OuterRef("pk")),
        ),
        maintenance_has_arena_training=Exists(
            ArenaVirtualReserveMember.objects.filter(
                profile_id=OuterRef("pk"),
                state=ArenaVirtualReserveMember.State.TRAINING,
                demand__status=ArenaVirtualDemand.Status.ACTIVE,
            ),
        ),
    )


def _external_reconciliation_state(profile: Any) -> tuple[bool, bool]:
    unresolved = getattr(profile, _HAS_UNRESOLVED_RECONCILIATION, None)
    applied = getattr(profile, _HAS_APPLIED_RECONCILIATION, None)
    if isinstance(unresolved, bool) and isinstance(applied, bool):
        return unresolved, applied

    statuses = set(
        BotExternalStrengthReconciliation.objects.filter(profile_id=int(profile.id)).values_list("status", flat=True)
    )
    return (
        any(status != BotExternalStrengthReconciliation.Status.APPLIED for status in statuses),
        BotExternalStrengthReconciliation.Status.APPLIED in statuses,
    )


def _current_human_strength_cap(
    profile: Any,
    *,
    now,
) -> StrengthSummary:
    config = load_virtual_player_v2_config()
    if config is None:
        raise ArenaStrengthProtectionError("bot_development_v2 is not configured")
    if (
        int(profile.engine_version) != config.engine_version
        or int(profile.rng_version) != config.rng_version
        or int(profile.plan_schema_version) != config.plan_schema_version
    ):
        raise ArenaStrengthProtectionError("profile identity does not match the configured V2 runtime")

    configured_policy = config.policy(int(profile.policy_version))
    if configured_policy.checksum != str(profile.policy_checksum):
        raise ArenaStrengthProtectionError("profile policy checksum does not match configuration")
    get_policy_release(
        version=int(profile.policy_version),
        expected_checksum=str(profile.policy_checksum),
    )

    manor = profile.manor
    band = config.band_for_prestige(int(manor.prestige or 0))
    if band.name != str(profile.current_prestige_band):
        raise ArenaStrengthProtectionError("profile current prestige band does not match Manor prestige")

    context = RandomContext(
        rng_version=int(profile.rng_version),
        growth_seed=int(profile.growth_seed),
        engine_version=int(profile.engine_version),
        plan_schema_version=int(profile.plan_schema_version),
        policy_version=int(profile.policy_version),
        maintenance_sequence=int(profile.maintenance_sequence),
    )
    _control_version, selection, _control_digest = growth_control_reference_selection(
        manor_strength=load_manor_strength_summary(manor_id=int(profile.manor_id)),
        context=context,
        region=str(manor.region),
        prestige_band=band.name,
        now=now,
    )
    return selection.cap


def is_virtual_profile_arena_match_eligible(
    profile: Any,
    *,
    now=None,
) -> bool:
    """Fail closed for unresolved intents and applied results above the live cap."""
    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        raise ValueError("now must be timezone-aware")

    has_unresolved, has_applied = _external_reconciliation_state(profile)
    if has_unresolved:
        return False
    if not has_applied:
        return True

    try:
        cap = _current_human_strength_cap(profile, now=current_time)
        current_strength = load_manor_strength_summary(manor_id=int(profile.manor_id))
        validate_strength_within_cap(current_strength, cap)
    except (
        ArenaStrengthProtectionError,
        PolicyRegistryError,
        ProjectionRuleError,
        ReferenceSnapshotError,
        UnsupportedRandomDomainError,
        UnsupportedRngVersionError,
        VirtualPlayerConfigError,
    ):
        return False
    return True


def arena_protected_bot_manor_ids() -> set[int]:
    protected = set(
        ArenaEntry.objects.filter(
            status=ArenaEntry.Status.REGISTERED,
            tournament__status__in=[
                ArenaTournament.Status.RECRUITING,
                ArenaTournament.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    protected.update(
        ArenaCoopEntry.objects.filter(
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    return protected


def is_virtual_profile_arena_protected(*, profile_id: int, manor_id: int) -> bool:
    if ArenaVirtualReserveMember.objects.filter(profile_id=profile_id).exists():
        return True
    return int(manor_id) in arena_protected_bot_manor_ids()


def is_virtual_profile_arena_training(*, profile_id: int) -> bool:
    """Return whether an active arena reserve currently owns this profile's growth slot."""

    return ArenaVirtualReserveMember.objects.filter(
        profile_id=profile_id,
        state=ArenaVirtualReserveMember.State.TRAINING,
        demand__status=ArenaVirtualDemand.Status.ACTIVE,
    ).exists()


__all__ = [
    "arena_protected_bot_manor_ids",
    "is_virtual_profile_arena_match_eligible",
    "is_virtual_profile_arena_protected",
    "is_virtual_profile_arena_training",
    "with_arena_reserve_guard",
    "with_arena_reconciliation_state",
]
