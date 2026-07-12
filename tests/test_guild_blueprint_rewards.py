import pytest

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember
from guilds.services.blueprint_rewards import claim_guild_blueprint_reward


class _Blueprint:
    key = "blueprint_xiaoweitoukie"
    rarity = "blue"


@pytest.mark.django_db
def test_claim_guild_blueprint_reward_uses_member_owned_weekly_cap(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="guild_blueprint_claim", password="pass123")
    ensure_manor(user)
    guild = Guild.objects.create(name="图纸帮会", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, weekly_contribution=1)

    monkeypatch.setattr(
        "guilds.services.blueprint_rewards.load_blueprint_catalog", lambda: {_Blueprint.key: _Blueprint()}
    )
    monkeypatch.setattr(
        "guilds.services.blueprint_rewards.load_guild_rules",
        lambda: {"blueprint_rewards": {"choices": {"blue": [_Blueprint.key]}}},
    )
    granted = []
    monkeypatch.setattr(
        "guilds.services.blueprint_rewards.add_item_to_inventory_locked",
        lambda manor, item_key, quantity: granted.append((manor, item_key, quantity)),
    )

    claim = claim_guild_blueprint_reward(member, _Blueprint.key)

    assert claim.blueprint_key == _Blueprint.key
    assert granted == [(user.manor, _Blueprint.key, 1)]
