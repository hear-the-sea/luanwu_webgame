"""增量战斗配置与当前 YAML 一致，更新模板不改玩家资产或画像。"""

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from gameplay.models import MissionTemplate
from guests.models import Guest, GuestTemplate, WorldUniqueGuest

rules = import_module("gameplay.migrations.0227_zhanlong_current_combat_profiles")
base = import_module("gameplay.migrations.0220_zhanlong_quest_chain")
exchange = import_module("gameplay.migrations.0221_chiyou_rewards_and_unique_guests")
DATA = Path(__file__).resolve().parents[1] / "data"
STAT_FIELDS = {
    "force": "base_attack",
    "intellect": "base_intellect",
    "defense": "base_defense",
    "agility": "base_agility",
    "luck": "base_luck",
    "base_hp": "base_hp",
}


def test_final_migration_profiles_match_all_four_missions_and_ten_guest_templates():
    missions = {row["key"]: row for row in yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]}
    for key, defaults in base.MISSION_DEFAULTS.items():
        final = {**defaults, **rules.MISSION_UPDATES.get(key, {})}
        assert {field: missions[key].get(field) for field in final} == final
    all_defaults = {**base.ENEMY_DEFAULTS, **exchange.GUEST_DEFAULTS}
    changed = {}
    for filename in ("zhanlong.yaml", "chiyou_exchange.yaml"):
        data = yaml.safe_load((DATA / "guests" / filename).read_text())["heroes"]
        for roster in data.values():
            for hero in roster:
                defaults = all_defaults[hero["key"]]
                delta = {
                    STAT_FIELDS[stat]: value
                    for stat, value in hero["custom_stats"].items()
                    if defaults[STAT_FIELDS[stat]] != value
                }
                if delta:
                    changed[hero["key"]] = delta
                final = {**defaults, **rules.GUEST_UPDATES.get(hero["key"], {})}
                assert {stat: final[field] for stat, field in STAT_FIELDS.items()} == hero["custom_stats"]
    assert rules.GUEST_UPDATES == changed


@pytest.mark.django_db
def test_profile_sync_is_repeatable_and_only_updates_explicit_template_fields(manor_factory):
    manor, _ = manor_factory(username="current_zhanlong_profiles")
    for key, updates in rules.GUEST_UPDATES.items():
        GuestTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "avatar": "guests/keep_portrait.webp",
                "base_attack": 41,
                "is_world_unique": key.startswith("hero_"),
            },
        )
    for key in rules.MISSION_UPDATES:
        MissionTemplate.objects.update_or_create(key=key, defaults={"name": key, "enemy_troops": {"qiang_wang": 1}})
    template = GuestTemplate.objects.get(key="hero_yang_zheng_purple")
    guest = Guest.objects.create(manor=manor, template=template, luck=12, current_hp=321)
    state, _ = WorldUniqueGuest.objects.update_or_create(
        template=template, defaults={"status": "serving", "owner_guest": guest, "owner_manor": manor, "version": 7}
    )
    guest_before = Guest.objects.filter(pk=guest.pk).values().get()
    state_before = WorldUniqueGuest.objects.filter(pk=state.pk).values().get()
    before = {row["key"]: row for row in GuestTemplate.objects.filter(key__in=rules.GUEST_UPDATES).values()}
    for _ in range(2):
        rules.sync_current_combat_profiles(apps, SimpleNamespace(connection=connection))
    for row in GuestTemplate.objects.filter(key__in=rules.GUEST_UPDATES).values():
        assert row == {**before[row["key"]], **rules.GUEST_UPDATES[row["key"]]}
    for mission in MissionTemplate.objects.filter(key__in=rules.MISSION_UPDATES):
        assert mission.enemy_troops == rules.MISSION_UPDATES[mission.key]["enemy_troops"]
    assert Guest.objects.filter(pk=guest.pk).values().get() == guest_before
    assert WorldUniqueGuest.objects.filter(pk=state.pk).values().get() == state_before
