"""把重复门客模板并入保留版本，避免加载器删除模板时丢玩家门客。"""

from __future__ import annotations

from importlib import import_module

from django.db import migrations

merge_helpers = import_module("guests.migrations.0096_merge_cheng_zhijie_into_cheng_yaojin")

# (duplicate_key, canonical_key)
DUPLICATE_TEMPLATES = (
    ("hist_sljnbc_0380", "hist_sljnbc_0381"),  # 绿版袁术 -> 蓝版袁术
    ("hist_sljnbc_0382", "hist_sljnbc_0383"),  # 绿版马谡 -> 蓝版马谡
    ("hist_suitang_0266", "hist_suitang_0055_yuchigong"),  # 尉迟敬德 -> 尉迟恭
)


def merge_duplicate_templates(apps, schema_editor):
    merge_helpers.merge_duplicate_template_pairs(apps, schema_editor, DUPLICATE_TEMPLATES)


class Migration(migrations.Migration):
    dependencies = [("guests", "0096_merge_cheng_zhijie_into_cheng_yaojin")]

    operations = [
        migrations.RunPython(merge_duplicate_templates, migrations.RunPython.noop),
    ]
