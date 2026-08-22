from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0071_guestrecruitment_virtual_source"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="recruitmentcandidate",
            index=models.Index(fields=["created_at", "id"], name="guest_candidate_expiry_idx"),
        ),
    ]
