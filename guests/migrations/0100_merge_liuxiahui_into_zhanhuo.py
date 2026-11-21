"""把重复的柳下惠模板并入保留的展获模板（同一人，显示名统一为柳下惠）。"""

from __future__ import annotations

from importlib import import_module

from django.db import migrations

merge_helpers = import_module("guests.migrations.0096_merge_cheng_zhijie_into_cheng_yaojin")

DUPLICATE_KEY = "hist_xianqin_0506"
CANONICAL_KEY = "hist_xianqin_0383"


def merge_duplicate_template(apps, schema_editor):
    merge_helpers.merge_duplicate_template_pairs(apps, schema_editor, ((DUPLICATE_KEY, CANONICAL_KEY),))


class Migration(migrations.Migration):
    dependencies = [("guests", "0099_merge_duplicate_xianqin_guests")]

    operations = [
        migrations.RunPython(merge_duplicate_template, migrations.RunPython.noop),
    ]
