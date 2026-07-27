from django.db import migrations, models
from django.db.models import Count, F, Q


def audit_arena_match_integrity(apps, schema_editor):
    ArenaMatch = apps.get_model("gameplay", "ArenaMatch")
    duplicate_slots = list(
        ArenaMatch.objects.values("tournament_id", "round_number", "match_index")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .order_by("tournament_id", "round_number", "match_index")[:20]
    )
    cross_tournament_ids = list(
        ArenaMatch.objects.filter(
            ~Q(attacker_entry__tournament_id=F("tournament_id"))
            | (Q(defender_entry__isnull=False) & ~Q(defender_entry__tournament_id=F("tournament_id")))
            | (Q(winner_entry__isnull=False) & ~Q(winner_entry__tournament_id=F("tournament_id")))
        )
        .order_by("id")
        .values_list("id", flat=True)[:20]
    )
    invalid_winner_ids = list(
        ArenaMatch.objects.filter(winner_entry__isnull=False)
        .exclude(Q(winner_entry_id=F("attacker_entry_id")) | Q(winner_entry_id=F("defender_entry_id")))
        .order_by("id")
        .values_list("id", flat=True)[:20]
    )
    if duplicate_slots or cross_tournament_ids or invalid_winner_ids:
        raise RuntimeError(
            "ArenaMatch integrity audit failed before unique_arena_match_slot; "
            f"duplicate_slots={duplicate_slots}, "
            f"cross_tournament_match_ids={cross_tournament_ids}, "
            f"invalid_winner_match_ids={invalid_winner_ids}. "
            "Run audit_pvp_arena_state in dry-run mode and repair through domain services first."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0136_alter_botprofile_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenatournament",
            name="base_seed",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="arenatournament",
            name="battle_engine_version",
            field=models.CharField(default="legacy", max_length=16),
        ),
        migrations.AddField(
            model_name="arenatournament",
            name="rng_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="arenamatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("scheduled", "待结算"),
                    ("completed", "已完成"),
                    ("forfeit", "弃权"),
                    ("bye", "轮空"),
                ],
                default="scheduled",
                max_length=16,
            ),
        ),
        migrations.RunPython(audit_arena_match_integrity, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="arenamatch",
            constraint=models.UniqueConstraint(
                fields=("tournament", "round_number", "match_index"),
                name="unique_arena_match_slot",
            ),
        ),
    ]
