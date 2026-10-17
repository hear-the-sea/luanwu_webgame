"""Fix equipment variants when a new operator reward is created, before delivery."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from django.db import transaction

from gameplay.models import ItemTemplate
from gameplay.services.equipment_affixes import roll_equipment_reward_items_locked


def roll_new_equipment_reward_attachments_locked(attachments: Mapping[str, Any]) -> dict[str, Any]:
    """Roll each new equipment reward; callers must never use this for transfers."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("equipment attachment generation requires transaction.atomic()")
    actual = copy.deepcopy(dict(attachments))
    for bucket_name in ("items", "bound_items"):
        bucket = actual.get(bucket_name)
        if not isinstance(bucket, dict) or not bucket:
            continue
        # Historical mail accepts item keys before their templates are loaded.
        # Resolve known equipment only, keeping that delivery contract intact.
        templates = {
            template.key: template
            for template in ItemTemplate.objects.filter(key__in=bucket, effect_type__startswith="equip_")
        }
        if not templates:
            continue
        equipment = {key: bucket[key] for key in templates}
        rolled, _templates = roll_equipment_reward_items_locked(equipment, templates=templates)
        actual[bucket_name] = {key: value for key, value in bucket.items() if key not in templates}
        for key, amount in rolled.items():
            actual[bucket_name][key] = actual[bucket_name].get(key, 0) + amount
    return actual
