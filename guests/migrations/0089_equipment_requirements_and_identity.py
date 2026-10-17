from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guests", "0088_recruitment_pity")]

    operations = [
        migrations.AddField(
            model_name="geartemplate", name="required_level", field=models.PositiveIntegerField(default=1)
        ),
        migrations.AddField(
            model_name="geartemplate", name="base_key", field=models.SlugField(blank=True, default="", max_length=64)
        ),
        migrations.AddField(
            model_name="geartemplate", name="base_name", field=models.CharField(blank=True, default="", max_length=64)
        ),
    ]
