from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0069_recruitmentextraattempt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guestskill",
            name="source",
            field=models.CharField(
                choices=[
                    ("template", "模板"),
                    ("book", "技能书"),
                    ("virtual", "虚拟投影"),
                ],
                default="template",
                max_length=16,
            ),
        ),
    ]
