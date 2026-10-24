from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from battle.arena_coop import adjust_arena_coop_damage
from battle.combatants_pkg.guest_builder import _serialize_skill
from guests.management.commands.sync_mingjiao_passives import SKILL_KEYS
from guests.models import GuestTemplate, Skill
from tests.test_battle_damage_invariants import _make_guest
from tests.test_mingjiao_passive_roles import begin_round, make_team


@pytest.mark.django_db
def test_sync_mingjiao_preserves_skill_links_and_other_configuration():
    existing = Skill.objects.create(key="gl_top_mingjiao_command", name="旧号令", kind="passive")
    boss = GuestTemplate.objects.create(key="arena_gl_top_zhang_wuji_boss", name="张无忌")
    boss.initial_skills.add(existing)
    other = Skill.objects.create(key="unrelated_mingjiao_sync", name="其它技能", description="保留原配置")
    before = set(Skill.objects.values_list("key", flat=True))
    call_command("sync_mingjiao_passives", stdout=StringIO())
    assert set(Skill.objects.values_list("key", flat=True)) == before | SKILL_KEYS
    assert list(boss.initial_skills.values_list("pk", flat=True)) == [existing.pk]
    other.refresh_from_db()
    assert other.description == "保留原配置"
    first = list(Skill.objects.filter(key__in=SKILL_KEYS).order_by("key").values())
    call_command("sync_mingjiao_passives", stdout=StringIO())
    assert list(Skill.objects.filter(key__in=SKILL_KEYS).order_by("key").values()) == first

    skills = [_serialize_skill(skill) for skill in Skill.objects.filter(key__in=SKILL_KEYS)]
    actor, team = make_team(skills, 40000, 4)
    attacker = _make_guest(name="攻击者", side="attacker", hp=100000)
    begin_round(actor, team, attacker, 1, 0.99)
    assert adjust_arena_coop_damage(actor, attacker, 10000) == pytest.approx(18480)
    assert adjust_arena_coop_damage(attacker, actor, 32000) == 32000
    assert "reflect_name" not in actor.battle_modifiers


@pytest.mark.django_db
def test_mingjiao_dry_run_preserves_database():
    before = list(Skill.objects.order_by("pk").values())
    call_command("sync_mingjiao_passives", dry_run=True, stdout=StringIO())
    assert list(Skill.objects.order_by("pk").values()) == before


@pytest.mark.django_db
def test_mingjiao_rejects_missing_skill_before_writing(monkeypatch):
    from guests.management.commands.load_guest_templates import Command as TemplateLoader

    monkeypatch.setattr(TemplateLoader, "_load_payload", lambda *args: {"skills": []})
    before = list(Skill.objects.order_by("pk").values())
    with pytest.raises(CommandError, match="各配置一次"):
        call_command("sync_mingjiao_passives", stdout=StringIO())
    assert list(Skill.objects.order_by("pk").values()) == before
