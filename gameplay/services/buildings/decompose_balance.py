from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

MAX_DUPLICATES_PER_CRAFT = {"blue": 4.0, "purple": 5.0, "orange": 6.0}
SOURCE_RARITY = {"blue": "green", "purple": "blue", "orange": "purple"}


def simulate_decompose_means(
    config: dict[str, Any], rarity: str, *, rolls: int = 100_000, seed: int = 0
) -> dict[str, float]:
    if rolls <= 0:
        raise ValueError("rolls must be positive")
    rng = random.Random(seed)
    totals: dict[str, float] = defaultdict(float)
    for _ in range(rolls):
        for key, bounds in config["base_materials"][rarity].items():
            totals[key] += rng.randint(int(bounds[0]), int(bounds[1]))
        for key, chance in config["chance_rewards"][rarity].items():
            if rng.random() < float(chance):
                totals[key] += 1
    return {key: amount / rolls for key, amount in totals.items()}


def estimated_duplicate_items_for_recipe(
    recipe: dict[str, Any], *, output_rarity: str, decompose_config: dict[str, Any]
) -> float:
    source_rarity = SOURCE_RARITY[output_rarity]
    means = simulate_decompose_means(decompose_config, source_rarity)
    required = recipe.get("costs") or {}
    counts = [float(amount) / means[key] for key, amount in required.items() if key in means and means[key] > 0]
    return max(counts, default=0.0)
