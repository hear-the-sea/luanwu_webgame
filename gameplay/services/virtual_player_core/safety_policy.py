"""Exact, fail-closed classification shared by monitoring and recovery."""

from django.conf import settings

from .config import V2_PRESTIGE_BAND_NAMES

POLICY_VERSION = "arena-scoped-v1"
ARENA_WARNING_CODES = frozenset(
    {
        "arena_shortage_baseline_missing",
        "arena_shortage_baseline_expired",
        "arena_shortage_baseline_population_exceeded",
        "arena_shortage_absolute_increase",
    }
)


def arena_warning_scope(reason: str) -> tuple[str, str] | None:
    parts = reason.split(":")
    if (
        len(parts) == 3
        and parts[0] in ARENA_WARNING_CODES
        and parts[1] in {"tournament", "coop"}
        and parts[2] in V2_PRESTIGE_BAND_NAMES
    ):
        return parts[1], parts[2]
    return None


def safety_policy_mode() -> str:
    mode = getattr(settings, "VIRTUAL_PLAYER_ARENA_SAFETY_POLICY", "scoped")
    if mode not in {"legacy", "shadow", "scoped"}:
        raise ValueError("VIRTUAL_PLAYER_ARENA_SAFETY_POLICY must be legacy, shadow or scoped")
    return mode


def classify_reasons(reasons: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Unknown codes/scopes remain global blockers, never prefix-match warnings."""
    unique = tuple(dict.fromkeys(reasons))
    return (
        tuple(reason for reason in unique if arena_warning_scope(reason) is None),
        tuple(reason for reason in unique if arena_warning_scope(reason) is not None),
    )


def reason_details(reasons: tuple[str, ...]) -> list[dict]:
    details = []
    for reason in dict.fromkeys(reasons):
        scope = arena_warning_scope(reason)
        details.append(
            {
                "reason": reason,
                "severity": "warning" if scope else "critical",
                "action": "observe" if scope else "pause",
                "scope": {"kind": scope[0], "prestige_band": scope[1]} if scope else {"kind": "global"},
            }
        )
    return details
