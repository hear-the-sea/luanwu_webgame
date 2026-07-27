from __future__ import annotations

from django.contrib import admin

from battle.models import BattleReport
from gameplay.models import ArenaCoopEvent, ArenaExchangeRecord, ArenaMatch, ArenaTournament, RaidRun
from guilds.models import GuildRaidRun


def test_replay_metadata_is_visible_and_read_only_in_operational_admins():
    replay_fields = {"base_seed", "rng_version", "battle_engine_version"}
    for model in [RaidRun, GuildRaidRun, ArenaTournament, ArenaMatch, ArenaCoopEvent]:
        assert model in admin.site._registry
        model_admin = admin.site._registry[model]
        assert replay_fields <= set(model_admin.list_display)
        assert replay_fields <= set(model_admin.readonly_fields)

    report_admin = admin.site._registry[BattleReport]
    assert {"seed", "rng_version", "battle_engine_version"} <= set(report_admin.list_display)
    assert {"seed", "rng_version", "battle_engine_version"} <= set(report_admin.readonly_fields)

    exchange_admin = admin.site._registry[ArenaExchangeRecord]
    assert {"replay_base_seed", "replay_rng_version"} <= set(exchange_admin.list_display)
    assert "payload" in exchange_admin.readonly_fields
