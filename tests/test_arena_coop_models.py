from gameplay.models import Manor


def test_manor_model_defines_arena_coop_daily_counter_fields():
    participations_field = Manor._meta.get_field("arena_coop_participations_today")
    participation_date_field = Manor._meta.get_field("arena_coop_participation_date")

    assert participations_field.default == 0
    assert participation_date_field.null is True
    assert participation_date_field.blank is True
