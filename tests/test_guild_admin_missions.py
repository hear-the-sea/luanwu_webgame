from __future__ import annotations

from types import SimpleNamespace

from django.contrib.admin.sites import AdminSite

from guilds.admin import GuildMissionRunAdmin
from guilds.models import GuildMissionRun


def test_guild_mission_run_admin_is_read_only():
    admin_obj = GuildMissionRunAdmin(GuildMissionRun, AdminSite())
    request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda *_args, **_kwargs: True))

    assert admin_obj.has_add_permission(request) is False
    assert admin_obj.has_change_permission(request) is False
