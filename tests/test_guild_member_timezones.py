from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone

from guilds.models import GuildMember


def test_guild_member_reset_defaults_are_local_dates():
    member = GuildMember(guild_id=1, user_id=1)

    for field_name in ("weekly_reset_at", "daily_donation_reset_at", "daily_exchange_reset_at"):
        value = getattr(member, field_name)
        assert type(value) is date
        assert not isinstance(value, datetime)
        assert value == timezone.localdate()
