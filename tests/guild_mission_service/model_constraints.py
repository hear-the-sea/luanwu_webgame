from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from battle.models import TroopTemplate
from guilds.models import GuildMissionRun, GuildMissionTemplate, GuildTroopStorage
from tests.guild_mission_service.support import create_guild_and_leader


class TestGuildMissionModelConstraints:
    @pytest.mark.django_db
    def test_guild_mission_run_exposes_status_constants(self):
        assert GuildMissionRun.Status.ACTIVE == "active"
        assert GuildMissionRun.Status.COMPLETED == "completed"
        assert GuildMissionRun.Status.RETREATED == "retreated"

    @pytest.mark.django_db
    def test_guild_mission_run_allows_only_one_active_run_per_guild(self, django_user_model):
        guild, leader = create_guild_and_leader(django_user_model, "unique_run")
        template = GuildMissionTemplate.objects.create(
            key="model_unique_run",
            name="模型唯一运行任务",
            description="smoke",
            difficulty="junior",
            task_type="guest",
            base_duration_seconds=300,
            ruby_reward=10,
            recommended_guest_count=1,
        )

        GuildMissionRun.objects.create(
            guild=guild,
            template=template,
            started_by=leader,
            status="active",
            selected_guest_count=1,
            ruby_reward=10,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                GuildMissionRun.objects.create(
                    guild=guild,
                    template=template,
                    started_by=leader,
                    status="active",
                    selected_guest_count=1,
                    ruby_reward=10,
                )

    @pytest.mark.django_db
    def test_guild_troop_storage_is_unique_per_template(self, django_user_model):
        guild, _leader = create_guild_and_leader(django_user_model, "unique_storage")
        troop_template = TroopTemplate.objects.create(key="guild_model_archer", name="模型弓兵")

        GuildTroopStorage.objects.create(
            guild=guild,
            troop_template=troop_template,
            count=100,
        )

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                GuildTroopStorage.objects.create(
                    guild=guild,
                    troop_template=troop_template,
                    count=50,
                )
