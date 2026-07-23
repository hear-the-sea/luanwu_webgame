from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0129_jail_persuasion_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="jailinteractionlog",
            name="attempt_scope",
            field=models.CharField(
                blank=True,
                default=None,
                max_length=24,
                null=True,
                verbose_name="尝试范围",
            ),
        ),
        migrations.AlterField(
            model_name="jailinteractionlog",
            name="outcome",
            field=models.CharField(
                choices=[
                    ("matched", "契合"),
                    ("neutral", "普通"),
                    ("taboo", "犯忌"),
                    ("failed", "失败"),
                    ("backfire", "反噬"),
                    ("event", "事件"),
                    ("recruited", "归附成功"),
                ],
                max_length=16,
                verbose_name="结果",
            ),
        ),
        migrations.AddConstraint(
            model_name="jailinteractionlog",
            constraint=models.UniqueConstraint(
                fields=("prisoner", "usage_date", "attempt_scope"),
                name="uniq_jail_attempt_scope_date",
            ),
        ),
    ]
