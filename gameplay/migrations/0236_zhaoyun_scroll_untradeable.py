"""将赵云招募卷轴设为不可交易。"""

from django.db import migrations


def make_zhaoyun_scroll_untradeable(apps, schema_editor):
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    ItemTemplate.objects.using(schema_editor.connection.alias).filter(key="zhaoyun_guest_scroll").update(
        tradeable=False
    )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0235_bridge_roar_without_dust_passive")]
    operations = [migrations.RunPython(make_zhaoyun_scroll_untradeable, migrations.RunPython.noop)]
