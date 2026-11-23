"""提升白门楼宝箱（普通与 v3）的超级门客易容卡掉率至 10%。"""

from __future__ import annotations

from copy import deepcopy

from django.db import migrations

BAIMENLOU_CHEST_KEYS = (
    "mission_chest_baimenlou_mingyun_jueze",
    "mission_chest_v3_baimenlou_mingyun_jueze",
)
OLD_DISGUISE_CARD_CHANCE = 0.01
NEW_DISGUISE_CARD_CHANCE = 0.1


def _sync_baimenlou_disguise_rate(apps, schema_editor, *, reverse: bool = False) -> None:
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    database = schema_editor.connection.alias
    target_chance = OLD_DISGUISE_CARD_CHANCE if reverse else NEW_DISGUISE_CARD_CHANCE
    for chest in ItemTemplate.objects.using(database).filter(key__in=BAIMENLOU_CHEST_KEYS):
        payload = deepcopy(chest.effect_payload or {})
        groups = payload.get("random_item_groups")
        if not isinstance(groups, list):
            continue
        changed = False
        for group in groups:
            if isinstance(group, dict) and group.get("label") == "super_guest_disguise_card":
                group["chance"] = target_chance
                changed = True
        if changed:
            chest.effect_payload = payload
            chest.save(using=database, update_fields=["effect_payload"])


def apply_baimenlou_disguise_rate(apps, schema_editor) -> None:
    _sync_baimenlou_disguise_rate(apps, schema_editor, reverse=False)


def revert_baimenlou_disguise_rate(apps, schema_editor) -> None:
    _sync_baimenlou_disguise_rate(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0280_boss_hp_rebalance"),
    ]

    operations = [
        migrations.RunPython(apply_baimenlou_disguise_rate, revert_baimenlou_disguise_rate, atomic=True),
    ]
