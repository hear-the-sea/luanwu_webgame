from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import GuildValidationError
from gameplay.models import InventoryItem
from guilds.models import GuildMember
from guilds.services import guild as guild_service

pytest_plugins = ("tests.guilds.fixtures",)


@pytest.mark.django_db
class TestGuildCreation:
    def test_create_guild_success(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="测试帮会", description="这是一个测试帮会")

        assert guild is not None
        assert guild.name == "测试帮会"
        assert guild.level == 1
        assert guild.is_active is True

        membership = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        assert membership.position == "leader"
        assert membership.is_active is True
        assert guild.technologies.count() == 9
        assert guild.technologies.get(tech_key="guild_lineup_capacity").max_level == 20
        assert guild.technologies.get(tech_key="guild_dispatch_capacity").max_level == 20

    def test_create_guild_sets_newbie_protection_from_runtime_rules(self, user_with_gold_bars, monkeypatch):
        protection_seconds = 3600
        monkeypatch.setattr("guilds.constants.GUILD_PVP_NEWBIE_PROTECTION_SECONDS", protection_seconds)
        before_create = timezone.now()

        guild = guild_service.create_guild(user=user_with_gold_bars, name="保护帮会", description="")
        after_create = timezone.now()

        assert guild.newbie_protection_until is not None
        lower_bound = before_create + timedelta(seconds=protection_seconds)
        upper_bound = after_create + timedelta(seconds=protection_seconds)
        assert lower_bound <= guild.newbie_protection_until <= upper_bound

    def test_create_guild_duplicate_name(self, user_with_gold_bars, django_user_model, gold_bar_template):
        guild_service.create_guild(user=user_with_gold_bars, name="唯一帮会", description="")

        user2 = django_user_model.objects.create_user(username="user2", password="pass12345")
        from gameplay.services.manor.core import ensure_manor

        manor2 = ensure_manor(user2)
        InventoryItem.objects.create(
            manor=manor2,
            template=gold_bar_template,
            quantity=10,
            storage_location="warehouse",
        )

        with pytest.raises(GuildValidationError, match="帮会名称已存在"):
            guild_service.create_guild(user=user2, name="唯一帮会", description="")

    def test_create_guild_invalid_name(self, user_with_gold_bars):
        with pytest.raises(GuildValidationError, match="至少需要"):
            guild_service.create_guild(user=user_with_gold_bars, name="A", description="")

        with pytest.raises(GuildValidationError, match="只能包含"):
            guild_service.create_guild(user=user_with_gold_bars, name="帮会@#$", description="")

    def test_create_guild_insufficient_gold(self, django_user_model, gold_bar_template):
        user = django_user_model.objects.create_user(username="poor_user", password="pass12345")
        from gameplay.services.manor.core import ensure_manor

        ensure_manor(user)

        with pytest.raises(GuildValidationError, match="金条不足"):
            guild_service.create_guild(user=user, name="穷人帮会", description="")

    def test_create_guild_uses_runtime_creation_cost(self, user_with_gold_bars, monkeypatch):
        monkeypatch.setattr("guilds.constants.GUILD_CREATION_COST", {"gold_bar": 11})

        with pytest.raises(GuildValidationError, match="金条不足，需要11金条"):
            guild_service.create_guild(user=user_with_gold_bars, name="动态成本帮会", description="")

    def test_calculate_guild_upgrade_cost_uses_runtime_base_cost(self, monkeypatch):
        monkeypatch.setattr("guilds.constants.GUILD_UPGRADE_BASE_COST", 12)

        assert guild_service.calculate_guild_upgrade_cost(1) == 12
        assert guild_service.calculate_guild_upgrade_cost(2) == 24
