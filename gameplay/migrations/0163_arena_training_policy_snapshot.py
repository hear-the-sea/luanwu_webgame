from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0162_arena_admission_probe_and_member_lease"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_training_policy_version",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="竞技场培养策略版本"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_training_policy_checksum",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="竞技场培养策略校验和"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_strength_segment",
            field=models.CharField(blank=True, default="", max_length=32, verbose_name="竞技场强度段"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_strength_envelope_digest",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="竞技场强度包络摘要"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_supply_prestige_band",
            field=models.CharField(blank=True, default="", max_length=32, verbose_name="竞技场供给声望段"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_supply_prestige_band_priority",
            field=models.JSONField(blank=True, default=list, verbose_name="竞技场供给声望段优先级"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="arena_supply_prestige",
            field=models.PositiveBigIntegerField(default=0, verbose_name="竞技场供给声望"),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualdemand",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        arena_training_policy_version=0,
                        arena_training_policy_checksum="",
                        arena_strength_segment="",
                        arena_strength_envelope_digest="",
                        arena_supply_prestige_band="",
                        arena_supply_prestige_band_priority=[],
                        arena_supply_prestige=0,
                    )
                    | (
                        models.Q(arena_training_policy_version__gte=1)
                        & ~models.Q(arena_training_policy_checksum="")
                        & (
                            models.Q(
                                status="blocked",
                                arena_strength_segment="",
                                arena_strength_envelope_digest="",
                                arena_supply_prestige_band="",
                                arena_supply_prestige_band_priority=[],
                                arena_supply_prestige=0,
                            )
                            | (
                                ~models.Q(arena_strength_segment="")
                                & ~models.Q(arena_strength_envelope_digest="")
                                & ~models.Q(arena_supply_prestige_band="")
                                & ~models.Q(arena_supply_prestige_band_priority=[])
                            )
                        )
                    )
                ),
                name="arena_vd_training_policy_snapshot_valid",
            ),
        ),
    ]
