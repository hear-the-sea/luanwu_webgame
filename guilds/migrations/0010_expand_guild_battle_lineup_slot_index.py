import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0009_backfill_guild_capacity_technologies"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildbattlelineupentry",
            name="slot_index",
            field=models.PositiveSmallIntegerField(
                help_text="帮会出战名单最多40名",
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(40)],
                verbose_name="出战位",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="guildbattlelineupentry",
            name="gbl_slot_range_ck",
        ),
        migrations.AddConstraint(
            model_name="guildbattlelineupentry",
            constraint=models.CheckConstraint(
                condition=models.Q(("slot_index__gte", 1), ("slot_index__lte", 40)),
                name="gbl_slot_range_ck",
            ),
        ),
    ]
