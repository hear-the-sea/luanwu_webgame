"""侦查术情报解锁规则，供侦察和技术展示共用。"""

from __future__ import annotations

from typing import Any

from core.utils import safe_non_negative_int

SCOUT_INTEL_SCHEMA_VERSION = 2
SCOUT_INTEL_MILESTONES = (
    (50, "护院详情", "每种护院的名称与准确数量"),
    (100, "门客详情", "防守门客的名字、品质与等级"),
    (150, "资产详情", "银两、仓库粮食与可掠夺物资概况"),
    (200, "防守详情", "门客属性与技能、护院武艺及城防等级"),
)


def normalize_scout_level(value: Any) -> int:
    return min(200, safe_non_negative_int(value, 0))


def get_scout_intel_progress(level: Any) -> dict[str, Any]:
    current_level = normalize_scout_level(level)
    milestones: list[dict[str, Any]] = [
        {"level": required_level, "name": name, "description": description}
        for required_level, name, description in SCOUT_INTEL_MILESTONES
    ]
    return {
        "unlocked": [milestone for milestone in milestones if milestone["level"] <= current_level],
        "next_unlock": next((milestone for milestone in milestones if milestone["level"] > current_level), None),
    }
