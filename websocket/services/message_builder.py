"""World chat text normalization compatibility API."""

from __future__ import annotations

from gameplay.services.chat import normalize_world_chat_text


def normalize_text(text: str) -> str:
    """Sanitize user-submitted chat text."""
    return normalize_world_chat_text(text)
