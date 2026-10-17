from django.db import migrations, models
from django.db.models import F, Q


def mark_legacy_disguises(apps, schema_editor):
    Guest = apps.get_model("guests", "Guest")
    special = Q(super_disguise_applied=True) | ~Q(avatar_override_key="")
    # 超级易容未覆盖普通头像资源，可保留这份原图。
    Guest.objects.using(schema_editor.connection.alias).filter(special).update(
        original_avatar_asset_key=F("avatar_asset_key")
    )
    # 普通易容历史记录未保存原图；随机生成的基础门客头像不属于易容。
    Guest.objects.using(schema_editor.connection.alias).exclude(special).exclude(avatar_asset_key="").exclude(
        template__key__in=["base_gray_civil", "base_gray_military"]
    ).update(original_avatar_asset_key="")


class Migration(migrations.Migration):
    dependencies = [("guests", "0086_promote_zhudi")]
    operations = [
        migrations.AddField(
            model_name="guest",
            name="original_avatar_asset_key",
            field=models.CharField(
                "易容前头像资源",
                max_length=255,
                blank=True,
                null=True,
                default=None,
                help_text="NULL 表示尚未易容，空字符串表示原图使用门客模板头像",
            ),
        ),
        migrations.AddField(
            model_name="guest",
            name="use_original_avatar",
            field=models.BooleanField("显示原形象", default=False),
        ),
        migrations.RunPython(mark_legacy_disguises, migrations.RunPython.noop),
    ]
