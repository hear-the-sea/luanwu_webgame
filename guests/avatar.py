"""门客头像解析，集中处理运行时头像覆盖。"""

from __future__ import annotations

from django.core.files.storage import default_storage

from .constants import SUPER_GUEST_DISGUISE_AVATAR_KEY, SUPER_GUEST_DISGUISE_AVATAR_PATH


def has_guest_avatar_override(guest: object) -> bool:
    """Return whether a guest currently has a supported avatar override."""
    override_key = getattr(guest, "avatar_override_key", "")
    return isinstance(override_key, str) and override_key.strip() == SUPER_GUEST_DISGUISE_AVATAR_KEY


def resolve_guest_avatar_url(guest: object) -> str:
    """Resolve a guest's effective avatar URL without exposing arbitrary storage paths."""
    if has_guest_avatar_override(guest):
        return default_storage.url(SUPER_GUEST_DISGUISE_AVATAR_PATH)

    avatar = getattr(getattr(guest, "template", None), "avatar", None)
    if not avatar:
        return ""
    try:
        return str(avatar.url or "")
    except (AttributeError, ValueError):
        return ""
