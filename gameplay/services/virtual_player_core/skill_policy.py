from __future__ import annotations

# These catalogs describe event/NPC-only abilities, not skills available to a
# virtual player's normal roster.  Keep the boundary centralized so bootstrap
# and later maintenance cannot disagree about the virtual skill surface.
VIRTUAL_PLAYER_EXCLUDED_SKILL_PREFIXES = (
    "gl_top_",
    "guild_",
    "wanxian_",
)


def is_virtual_player_skill_allowed(skill_key: str) -> bool:
    normalized = str(skill_key).strip()
    return bool(normalized) and not any(
        normalized.startswith(prefix) for prefix in VIRTUAL_PLAYER_EXCLUDED_SKILL_PREFIXES
    )


__all__ = [
    "VIRTUAL_PLAYER_EXCLUDED_SKILL_PREFIXES",
    "is_virtual_player_skill_allowed",
]
