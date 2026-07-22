from __future__ import annotations

METHODS = ("kindness", "bribe", "reason", "might")

PUBLISHED_COPY_KEYS = frozenset(
    {f"clue.{method}.{kind}.{index}" for method in METHODS for kind in ("subtle", "explicit") for index in range(1, 3)}
    | {
        f"feedback.{method}.{outcome}.{index}"
        for method in METHODS
        for outcome in (
            ("matched", "neutral", "taboo", "failed", "backfire")
            if method in {"reason", "might"}
            else ("matched", "neutral", "taboo")
        )
        for index in range(1, 6)
    }
    | {
        f"milestone.{method}.{threshold}.{suffix}"
        for method in METHODS
        for threshold in (35, 70)
        for suffix in ("prompt", "aligned", "alternative")
    }
    | {f"recruitment.{mode}.{index}" for mode in ("standard", "negotiated", "heartfelt") for index in range(1, 4)}
)
