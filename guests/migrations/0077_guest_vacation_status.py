from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guests", "0076_move_set_defense_to_attribute")]

    operations = [
        migrations.AlterField(
            model_name="guest",
            name="status",
            field=models.CharField(
                choices=[
                    ("idle", "空闲"),
                    ("vacation", "归闲中"),
                    ("working", "打工中"),
                    ("deployed", "出征中"),
                    ("arena", "竞技中"),
                    ("injured", "重伤"),
                ],
                default="idle",
                max_length=16,
            ),
        ),
    ]
