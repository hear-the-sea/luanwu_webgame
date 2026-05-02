from __future__ import annotations

import pytest


def _seed_basic_tech_upgrade_warehouse_costs(guild) -> None:
    from guilds.models import GuildWarehouse

    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=999999, contribution_cost=2)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=999999, contribution_cost=50)


@pytest.mark.django_db
def test_upgrade_technology_rechecks_operator_permission_inside_transaction(monkeypatch, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guilds.models import Guild, GuildMember, GuildTechnology
    from guilds.services import technology as technology_service
    from guilds.services.technology import upgrade_technology

    operator = django_user_model.objects.create_user(username="tech_operator_demoted", password="pass")
    ensure_manor(operator)
    founder = django_user_model.objects.create_user(username="tech_founder_demoted", password="pass")
    guild = Guild.objects.create(name="TechDemotedGuild", founder=founder, silver=999999, grain=0, gold_bar=0)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)
    membership = GuildMember.objects.create(guild=guild, user=operator, position="admin")
    _seed_basic_tech_upgrade_warehouse_costs(guild)

    real_get_active_membership = technology_service.get_active_membership

    def _demote_after_initial_check(*args, **kwargs):
        resolved = real_get_active_membership(*args, **kwargs)
        GuildMember.objects.filter(pk=membership.pk).update(position="member")
        return resolved

    monkeypatch.setattr("guilds.services.technology.get_active_membership", _demote_after_initial_check)
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)

    with pytest.raises(technology_service.GuildTechnologyError, match="只有帮主和管理员"):
        upgrade_technology(guild, "equipment_forge", operator)

    tech.refresh_from_db()
    guild.refresh_from_db()
    assert tech.level == 0
    assert guild.silver == 999999
