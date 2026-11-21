"""合并先秦重复门客模板：熊胜并入白公胜、郧公辛并入斗辛、绛灌并入周勃。"""

from __future__ import annotations

from importlib import import_module

from django.db import migrations

merge_helpers = import_module("guests.migrations.0096_merge_cheng_zhijie_into_cheng_yaojin")

# (duplicate_key, canonical_key)
DUPLICATE_TEMPLATES = (
    ("hist_xianqin_0346", "hist_xianqin_0335"),  # 熊胜 -> 白公胜（同一人）
    ("hist_xianqin_0347", "hist_xianqin_0349"),  # 郧公辛 -> 斗辛（同一人）
    ("hist_xianqin_0327", "hist_qinhan_0027"),  # 绛灌（周勃+灌婴并称）-> 周勃
)


def merge_duplicate_templates(apps, schema_editor):
    merge_helpers.merge_duplicate_template_pairs(apps, schema_editor, DUPLICATE_TEMPLATES)


class Migration(migrations.Migration):
    dependencies = [("guests", "0098_reduce_defense_hp_multiplier")]

    operations = [
        migrations.RunPython(merge_duplicate_templates, migrations.RunPython.noop),
    ]
