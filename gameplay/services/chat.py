"""
聊天服务层
"""

from __future__ import annotations

import html
import re

TRUMPET_ITEM_KEY = "small_trumpet"
WORLD_CHAT_TEXT_MAX_LENGTH = 200
_WORLD_CHAT_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_world_chat_text(text: str) -> str:
    """Normalize user-submitted world chat text for storage and delivery."""
    escaped = html.escape(text)
    cleaned = _WORLD_CHAT_CONTROL_CHARS_RE.sub("", escaped)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip()[:WORLD_CHAT_TEXT_MAX_LENGTH]
