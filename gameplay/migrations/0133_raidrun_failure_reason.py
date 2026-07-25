from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0132_worktemplate_attribute_requirements"),
    ]

    operations = [
        migrations.AlterField(
            model_name="raidrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("marching", "行军中"),
                    ("battling", "战斗中"),
                    ("returning", "返程中"),
                    ("completed", "已完成"),
                    ("retreated", "已撤退"),
                    ("failed", "出征失败"),
                ],
                default="marching",
                max_length=16,
                verbose_name="状态",
            ),
        ),
        migrations.AddField(
            model_name="raidrun",
            name="failure_reason",
            field=models.CharField(
                blank=True,
                choices=[("missing_attacker_lineup", "缺少出征门客与快照")],
                default="",
                max_length=64,
                verbose_name="失败原因",
            ),
        ),
    ]
