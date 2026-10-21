from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.template.loader import render_to_string

from battle.view_helpers import build_battle_prelude, build_rounds_display
from guests.models import GuestTemplate, Skill
from tests.test_bridge_zhangfei_fury import bridge
from tests.test_zhanlong_boss_skills import passives


def test_bridge_active_skill_keeps_low_health_transition_without_dust_passive():
    actor = bridge(hp=19999)
    events = []
    passives("round_start", actor, [actor], [], events)
    assert events == []
    passives("action_before", actor, [actor], [], events)
    assert actor.skills[0]["targets"] == 2
    assert len(actor.skills) == 1
    assert [event["effect"] for event in events] == ["虎啸破阵"]


def test_old_dust_notice_is_displayed_once_before_rounds_without_mutating_history():
    old = {"type": "passive", "effect": "疑兵扬尘", "side": "defender", "message": "百万疑兵"}
    fury = {"type": "passive", "effect": "虎啸破阵", "side": "defender"}
    rounds = [{"round": 1, "events": [old, fury]}, {"round": 2, "events": [deepcopy(old)]}]
    before = deepcopy(rounds)
    notice = build_battle_prelude(rounds, set())
    assert "疑兵" in notice
    display = build_rounds_display(rounds, {})
    assert display[0]["defender_events"] == [fury]
    assert display[1]["defender_events"] == []
    body = render_to_string("battle/report_detail.html", {"battle_rounds": display, "battle_prelude": notice})
    assert body.count(notice) == 1
    assert body.index(notice) < body.index('data-battle-round="1"')
    assert "疑兵扬尘" not in body
    assert "虎啸破阵" in body
    assert rounds == before


def test_new_bridge_report_has_prelude_and_other_zhangfei_does_not():
    assert build_battle_prelude([], {"task_zhanlong_zhangfei_bridge"})
    assert build_battle_prelude([], {"task_zhanlong_zhangfei"}) == ""


@pytest.mark.django_db
def test_merge_migration_is_repeatable_preserves_other_guests_and_historical_skill():
    old = import_module("gameplay.migrations.0233_bridge_zhangfei_fury")
    new = import_module("gameplay.migrations.0235_bridge_roar_without_dust_passive")
    for key, defaults in old.SKILL_DEFAULTS.items():
        Skill.objects.update_or_create(key=key, defaults=defaults)
    dust, _ = Skill.objects.update_or_create(key="zhanlong_dust_ruse", defaults={"name": "疑兵扬尘"})
    actor, _ = GuestTemplate.objects.get_or_create(key="task_zhanlong_zhangfei_bridge", defaults={"name": "张飞"})
    actor.initial_skills.set(Skill.objects.filter(key__in=old.GUEST_SKILLS[actor.key]))
    other, _ = GuestTemplate.objects.get_or_create(key="task_zhanlong_zhangfei", defaults={"name": "演武张飞"})
    other.initial_skills.set([dust])
    before = list(GuestTemplate.objects.order_by("pk").values())
    for _ in range(2):
        new.merge_bridge_roar(apps, SimpleNamespace(connection=connection))
    assert list(actor.initial_skills.values_list("key", flat=True)) == ["zhanlong_bridge_tiger_dragon_roar"]
    assert list(other.initial_skills.values_list("key", flat=True)) == ["zhanlong_dust_ruse"]
    assert Skill.objects.filter(pk=dust.pk).exists()
    assert list(GuestTemplate.objects.order_by("pk").values()) == before
