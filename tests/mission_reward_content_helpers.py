"""内容测试读取任务宝箱的真实物品池与概率预算。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def mission_reward_payload(mission, items=None) -> dict:
    if items is None:
        payload = yaml.safe_load((DATA_DIR / "item_templates.yaml").read_text(encoding="utf-8"))
        items = {row["key"]: row for row in payload["items"]}
    drops = mission["drop_table"] if isinstance(mission, dict) else mission.drop_table
    chest_keys = set(drops) - {"silver"}
    assert len(chest_keys) == 1
    chest_key = next(iter(chest_keys))
    assert drops[chest_key] == 1
    chest = items[chest_key]
    assert chest["effect_type"] == "loot_box"
    assert chest["is_usable"] is True
    assert chest["effect_payload"]["mission_reward_version"] == 1
    assert chest["effect_payload"]["reward_balance_version"] == 2
    return chest["effect_payload"]


def reward_item_keys(payload: dict) -> set[str]:
    keys = {row["item_key"] for row in payload.get("item_rewards", [])}
    for group in payload.get("random_item_groups", []):
        keys.update(choice["item_key"] for choice in group["choices"])
    keys.update(choice["item_key"] for choice in payload.get("gear_choices", []))
    return keys


def assert_fixed_reward(payload: dict, item_key: str, quantity: int) -> None:
    rewards = [row for row in payload.get("item_rewards", []) if row["item_key"] == item_key]
    assert rewards == [{"item_key": item_key, "min_quantity": quantity, "max_quantity": quantity}]


def assert_random_group(payload: dict, chance: float, quantity: int, choices: dict[str, float]) -> None:
    groups = [
        group
        for group in payload.get("random_item_groups", [])
        if {choice["item_key"] for choice in group["choices"]} == set(choices)
        and group["min_quantity"] == group["max_quantity"] == quantity
    ]
    assert len(groups) == 1
    group = groups[0]
    assert group["chance"] == pytest.approx(chance)
    assert group["min_quantity"] == group["max_quantity"] == quantity
    actual = {choice["item_key"]: choice["weight"] for choice in group["choices"]}
    actual_total = sum(actual.values())
    expected_total = sum(choices.values())
    assert {key: weight / actual_total for key, weight in actual.items()} == pytest.approx(
        {key: weight / expected_total for key, weight in choices.items()}
    )
