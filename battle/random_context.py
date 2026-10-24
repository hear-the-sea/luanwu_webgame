from __future__ import annotations

import hashlib
import random
import secrets
from dataclasses import dataclass
from typing import Any, cast

CURRENT_RNG_VERSION = 1
CURRENT_BATTLE_ENGINE_VERSION = "9"
# 保留既有版本 2 兼容入口；版本 3 的规则另按下面的版本集合启用。
PREVIOUS_BATTLE_ENGINE_VERSION = "2"
# 后续版本继续保留分队攻击、远程与攻城规则，以及版本 4 的护院优先行动。
GROUPED_ATTACK_ENGINE_VERSIONS = frozenset({"3", "4", "5", "6", "7", "8", "9"})
TROOP_FIRST_ENGINE_VERSIONS = frozenset({"4", "5", "6", "7", "8", "9"})
# 版本 5 引入护院掩护与门客清兵 20/3，旧战报保留原规则和随机数消耗。
GUEST_TROOP_BALANCE_ENGINE_VERSIONS = frozenset({"5", "6", "7", "8", "9"})
# 版本 6 的门客先结算技能增量，再独立选敌普攻，仍属于一次行动。
GUEST_BURST_ENGINE_VERSIONS = frozenset({"6", "7", "8", "9"})
# 版本 7 的枪王在攻击者行动结束后，每回合对同一战斗对象仅判定一次反击。
DEFERRED_QIANG_WANG_COUNTER_ENGINE_VERSIONS = frozenset({"7", "8", "9"})
# 版本 8 修复技能暴击标记、无技能追加伤害的被动次数及阵亡后普通回血。
GUEST_ACTION_CONSISTENCY_ENGINE_VERSIONS = frozenset({"8", "9"})
# 版本 9 将先锋比例调整为 20%；旧版本继续使用 25%。
VANGUARD_20_PERCENT_ENGINE_VERSIONS = frozenset({"9"})
LEGACY_RNG_VERSION = 0
LEGACY_BATTLE_ENGINE_VERSION = "legacy"
MAX_PERSISTED_SEED = 2_147_483_647

RNG_STREAM_COMBAT = "combat"
RNG_STREAM_AI_GROWTH = "ai_growth"
RNG_STREAM_LOOT = "loot"
RNG_STREAM_CAPTURE = "capture"
RNG_STREAM_TIE_BREAK = "tie_break"
RNG_STREAM_RARE_DROP = "rare_drop"

KNOWN_RNG_STREAMS = {
    RNG_STREAM_COMBAT,
    RNG_STREAM_AI_GROWTH,
    RNG_STREAM_LOOT,
    RNG_STREAM_CAPTURE,
    RNG_STREAM_TIE_BREAK,
    RNG_STREAM_RARE_DROP,
}


def generate_base_seed() -> int:
    """Generate a server-controlled seed that fits existing persisted integer fields."""

    return secrets.randbelow(MAX_PERSISTED_SEED - 1) + 1


def normalize_base_seed(seed: object | None) -> int:
    if seed is None:
        return generate_base_seed()
    if isinstance(seed, bool):
        raise AssertionError(f"invalid battle base seed: {seed!r}")
    try:
        normalized = int(cast(Any, seed))
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid battle base seed: {seed!r}") from exc
    if not 0 <= normalized <= MAX_PERSISTED_SEED:
        raise AssertionError(f"invalid battle base seed: {seed!r}")
    return normalized


def derive_stream_seed(
    base_seed: int,
    stream: str,
    *,
    rng_version: int = CURRENT_RNG_VERSION,
    discriminator: object | None = None,
) -> int:
    normalized_stream = str(stream or "").strip()
    if normalized_stream not in KNOWN_RNG_STREAMS:
        raise AssertionError(f"unknown battle RNG stream: {stream!r}")
    normalized_version = int(rng_version)
    if normalized_version <= LEGACY_RNG_VERSION:
        raise AssertionError(f"unsupported battle RNG version: {rng_version!r}")
    discriminator_text = "" if discriminator is None else str(discriminator)
    payload = (f"battle-rng:{normalized_version}:{int(base_seed)}:{normalized_stream}:{discriminator_text}").encode(
        "utf-8"
    )
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def derive_persisted_seed(
    base_seed: int,
    stream: str,
    *,
    rng_version: int = CURRENT_RNG_VERSION,
    discriminator: object | None = None,
) -> int:
    derived = derive_stream_seed(
        base_seed,
        stream,
        rng_version=rng_version,
        discriminator=discriminator,
    )
    return (derived % (MAX_PERSISTED_SEED - 1)) + 1


@dataclass(frozen=True, slots=True)
class BattleRandomContext:
    base_seed: int
    rng_version: int = CURRENT_RNG_VERSION

    @classmethod
    def create(
        cls,
        base_seed: object | None = None,
        *,
        rng_version: int = CURRENT_RNG_VERSION,
    ) -> "BattleRandomContext":
        return cls(base_seed=normalize_base_seed(base_seed), rng_version=int(rng_version))

    def rng(self, stream: str, *, discriminator: object | None = None) -> random.Random:
        return random.Random(
            derive_stream_seed(
                self.base_seed,
                stream,
                rng_version=self.rng_version,
                discriminator=discriminator,
            )
        )

    def persisted_seed(self, stream: str, *, discriminator: object | None = None) -> int:
        return derive_persisted_seed(
            self.base_seed,
            stream,
            rng_version=self.rng_version,
            discriminator=discriminator,
        )


def current_replay_metadata(base_seed: object | None = None) -> dict[str, int | str]:
    context = BattleRandomContext.create(base_seed, rng_version=CURRENT_RNG_VERSION)
    return {
        "base_seed": context.base_seed,
        "rng_version": context.rng_version,
        "battle_engine_version": CURRENT_BATTLE_ENGINE_VERSION,
    }
