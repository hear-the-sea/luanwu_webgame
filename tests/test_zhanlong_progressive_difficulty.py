"""战龙递进难度只同步任务与敌将模板，保留玩家资产及既有技能。"""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.cache import cache
from django.db import connection

from battle.models import TroopTemplate
from gameplay.models import InventoryItem, ItemTemplate, Manor, MissionTemplate, PlayerTroop
from guests.models import Guest, GuestSkill, GuestTemplate, Skill

rules = import_module("gameplay.migrations.0240_zhanlong_progressive_difficulty")
base = import_module("gameplay.migrations.0220_zhanlong_quest_chain")


@pytest.mark.django_db
def test_progression_sync_is_repeatable_and_preserves_player_assets_and_skills(
    manor_factory, django_capture_on_commit_callbacks
):
    manor, _client = manor_factory(username="zhanlong_progression_assets")
    marker = Skill.objects.create(
        key="zhanlong_progression_preserved_skill", name="保留技能", passive_config={"preserve": True}
    )
    for key in rules.GUEST_UPDATES:
        template, _ = GuestTemplate.objects.update_or_create(
            key=key,
            defaults={**base.ENEMY_DEFAULTS[key], "avatar": "guests/keep_portrait.webp"},
        )
        template.initial_skills.set([marker])
    for key in rules.MISSION_UPDATES:
        MissionTemplate.objects.update_or_create(key=key, defaults=base.MISSION_DEFAULTS[key])
    unrelated_guest = GuestTemplate.objects.create(
        key="zhanlong_progression_unrelated_guest", name="保留门客模板", base_attack=123
    )
    unrelated_mission = MissionTemplate.objects.create(
        key="zhanlong_progression_unrelated_mission", name="保留任务", enemy_troops={"qiang_wang": 17}
    )
    unrelated_guest.initial_skills.set([marker])
    template = GuestTemplate.objects.get(key="task_zhanlong_caochun")
    guest = Guest.objects.create(
        manor=manor, template=template, force=100, intellect=80, defense_stat=90, agility=35, luck=11, current_hp=321
    )
    GuestSkill.objects.create(guest=guest, skill=marker)
    token, _ = ItemTemplate.objects.update_or_create(
        key="zhanlong_white_horse_token", defaults=base.ITEM_DEFAULTS["zhanlong_white_horse_token"]
    )
    inventory = InventoryItem.objects.create(manor=manor, template=token, quantity=7, bound_quantity=3)
    troop_template = TroopTemplate.objects.create(key="zhanlong_progression_preserved_troop", name="保留护院")
    troops = PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=123)

    guest_templates_before = {
        row["key"]: row for row in GuestTemplate.objects.filter(key__in=rules.GUEST_UPDATES).values()
    }
    missions_before = {
        row["key"]: row for row in MissionTemplate.objects.filter(key__in=rules.MISSION_UPDATES).values()
    }
    skill_links_before = list(GuestTemplate.initial_skills.through.objects.order_by("pk").values())
    preserved_rows = (
        (Manor, manor.pk),
        (Guest, guest.pk),
        (InventoryItem, inventory.pk),
        (PlayerTroop, troops.pk),
        (ItemTemplate, token.pk),
        (Skill, marker.pk),
        (GuestTemplate, unrelated_guest.pk),
        (MissionTemplate, unrelated_mission.pk),
    )
    assets_before = [model.objects.filter(pk=pk).values().get() for model, pk in preserved_rows]
    guest_skills_before = list(GuestSkill.objects.filter(guest=guest).values())
    counts_before = (GuestTemplate.objects.count(), MissionTemplate.objects.count())

    # 每次都从旧值恢复缓存，验证重复同步仍会使新战斗读取最新模板。
    for _ in range(2):
        cache.set("battle:guest_templates", ["stale_zhanlong_template"])
        with django_capture_on_commit_callbacks(execute=True):
            rules.sync_zhanlong_progression(apps, SimpleNamespace(connection=connection))
        assert cache.get("battle:guest_templates") is None
        for row in GuestTemplate.objects.filter(key__in=rules.GUEST_UPDATES).values():
            assert row == {**guest_templates_before[row["key"]], **rules.GUEST_UPDATES[row["key"]]}
        for row in MissionTemplate.objects.filter(key__in=rules.MISSION_UPDATES).values():
            assert row == {**missions_before[row["key"]], **rules.MISSION_UPDATES[row["key"]]}
        assert [model.objects.filter(pk=pk).values().get() for model, pk in preserved_rows] == assets_before
        assert list(GuestSkill.objects.filter(guest=guest).values()) == guest_skills_before
        assert list(GuestTemplate.initial_skills.through.objects.order_by("pk").values()) == skill_links_before
        assert (GuestTemplate.objects.count(), MissionTemplate.objects.count()) == counts_before
