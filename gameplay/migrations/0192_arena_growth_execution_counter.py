from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0191_arena_runtime_budget")]

    operations = [
        migrations.AlterField(
            model_name="arenavirtualreservemember",
            name="growth_execution_attempt_count",
            field=models.PositiveBigIntegerField(default=0, verbose_name="累计实际执行次数"),
        ),
    ]
