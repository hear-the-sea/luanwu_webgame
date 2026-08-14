from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar, cast

T = TypeVar("T")


@dataclass(slots=True)
class DeferredCandidateStage(Generic[T]):
    """Materialize one candidate stage at most once.

    The stage builder is deliberately unaware of selection or execution.  It
    only turns an immutable planning snapshot into a cached value.  Keeping
    this boundary small makes deferred planning safe to use without moving
    database writes or lock-time validation into the planning path.
    """

    name: str
    builder: Callable[[], T]
    _materialized: bool = field(default=False, init=False, repr=False)
    _value: T | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        normalized_name = str(self.name).strip()
        if not normalized_name:
            raise ValueError("candidate stage name must not be empty")
        if not callable(self.builder):
            raise TypeError("candidate stage builder must be callable")
        self.name = normalized_name

    @property
    def materialized(self) -> bool:
        return self._materialized

    def materialize(self) -> T:
        if not self._materialized:
            value = self.builder()
            self._value = value
            self._materialized = True
        return cast(T, self._value)


__all__ = ["DeferredCandidateStage"]
