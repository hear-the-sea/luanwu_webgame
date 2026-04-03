from __future__ import annotations

from typing import Any


class _Recorder:
    def __init__(self, sink: list[str] | None = None) -> None:
        self.sink = sink if sink is not None else []

    def write(self, message: Any) -> None:
        self.sink.append(str(message))
