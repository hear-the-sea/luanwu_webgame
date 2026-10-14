from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS

_SCOPE_ATTR = "_account_delete_message_scope"
_TOKEN = object()
_MISSING = object()


@contextmanager
def account_message_cascade_scope(request, manor_ids: Iterable[int]) -> Iterator[None]:
    """只在账号删除关联检查期间允许清理目标庄园的受保护消息。"""
    previous = getattr(request, _SCOPE_ATTR, _MISSING)
    setattr(request, _SCOPE_ATTR, (_TOKEN, frozenset(manor_ids)))
    try:
        yield
    finally:
        if previous is _MISSING:
            delattr(request, _SCOPE_ATTR)
        else:
            setattr(request, _SCOPE_ATTR, previous)


def is_account_message_cascade(request, message) -> bool:
    scope = getattr(request, _SCOPE_ATTR, None)
    return (
        isinstance(scope, tuple)
        and len(scope) == 2
        and scope[0] is _TOKEN
        and (message._state.db or DEFAULT_DB_ALIAS) == DEFAULT_DB_ALIAS
        and message.manor_id in scope[1]
    )
