from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0150_botarenashortagebaseline_expires_at_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="manor",
            index=models.Index(
                fields=["resource_updated_at", "id"],
                name="manor_resource_updated_idx",
            ),
        ),
    ]
