"""为模板稀有度/文武或成长区间变更后的已有门客重新匹配初始与成长属性，并补偿降档玩家。"""

from __future__ import annotations

from django.db import migrations

from guests.services.template_reconciliation import (
    reconcile_mismatched_guests,
    send_downgraded_guest_compensation_messages,
)


def apply_reconcile_guest_attributes(apps, schema_editor) -> None:
    database = schema_editor.connection.alias
    Guest = apps.get_model("guests", "Guest")
    BotProfile = apps.get_model("gameplay", "BotProfile")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Message = apps.get_model("gameplay", "Message")

    downgraded_manor_ids: set[int] = set()
    reconcile_mismatched_guests(
        guest_model=Guest,
        database=database,
        downgraded_manor_ids=downgraded_manor_ids,
    )
    send_downgraded_guest_compensation_messages(
        manor_ids=downgraded_manor_ids,
        bot_profile_model=BotProfile,
        item_template_model=ItemTemplate,
        message_model=Message,
        database=database,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0101_archetype_level_hp_gain"),
        ("gameplay", "0279_equipment_callback_rebalance"),
    ]

    operations = [
        migrations.RunPython(apply_reconcile_guest_attributes, migrations.RunPython.noop, atomic=True),
    ]
