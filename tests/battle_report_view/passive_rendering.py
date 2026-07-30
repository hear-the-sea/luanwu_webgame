from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse
from django.utils.html import strip_tags

from gameplay.services.manor.core import ensure_manor
from tests.battle_report_view.support import create_report


def _normalized_report_text(body: str) -> str:
    return "".join(strip_tags(body).split())


@pytest.mark.django_db
def test_report_view_renders_only_friendly_round_state_between_name_and_skills(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="battle_report_state_user",
        password="pass123",
        email="battle_report_state_user@test.local",
    )
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="敌方庄园",
        battle_type="task1",
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "side": "attacker",
                        "order": 1,
                        "actor": "赵云",
                        "target": "铁甲枪王",
                        "damage": 100,
                        "is_crit": True,
                        "is_dodge": False,
                        "skills": ["龙胆"],
                        "status_inflicted": [],
                        "kills": 2,
                        "target_defeated": False,
                        "actor_state": {
                            "kind": "guest",
                            "side": "attacker",
                            "percent": 72,
                            "status": "healthy",
                            "status_label": "状态充足",
                        },
                        "target_state": {
                            "kind": "troop",
                            "side": "defender",
                            "percent": 40,
                            "status": "warning",
                            "status_label": "状态偏低",
                        },
                    },
                    {
                        "side": "defender",
                        "order": 2,
                        "actor": "铁甲枪王",
                        "target": "赵云",
                        "damage": 80,
                        "is_crit": False,
                        "is_dodge": False,
                        "skills": [],
                        "status_inflicted": [],
                        "kills": 0,
                        "target_defeated": False,
                        "actor_state": {
                            "kind": "troop",
                            "side": "defender",
                            "percent": 40,
                            "status": "warning",
                            "status_label": "状态偏低",
                        },
                        "target_state": {
                            "kind": "guest",
                            "side": "attacker",
                            "percent": 55,
                            "status": "healthy",
                            "status_label": "状态充足",
                        },
                    },
                ],
            }
        ],
    )

    assert client.login(username="battle_report_state_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert body.count('data-unit-state-side="attacker"') == 1
    assert 'data-unit-state-side="defender"' not in body
    damage_summaries = BeautifulSoup(body, "html.parser").select(".event-damage-summary")
    assert len(damage_summaries) == 2
    assert all(summary.select_one(".battle-unit-state") is None for summary in damage_summaries)
    event_start = body.index('class="event-unit-summary"')
    event_end = body.index("</div>", event_start)
    event_markup = body[event_start:event_end]
    assert event_markup.index("event-unit-name") < event_markup.index("battle-unit-state")
    assert event_markup.index("battle-unit-state") < event_markup.index("event-unit-skills")


@pytest.mark.django_db
def test_report_view_renders_charging_actor_state(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="battle_report_charging_user",
        password="pass123",
        email="battle_report_charging_user@test.local",
    )
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="敌方庄园",
        battle_type="task1",
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "side": "attacker",
                        "order": 1,
                        "actor": "蓄势待发的长名门客",
                        "status": "charging",
                        "message": "冲锋中",
                        "actor_state": {
                            "kind": "guest",
                            "side": "attacker",
                            "percent": 68,
                            "status": "healthy",
                            "status_label": "状态充足",
                        },
                    },
                    {
                        "side": "defender",
                        "order": 2,
                        "actor": "敌方冲锋门客",
                        "status": "charging",
                        "message": "冲锋中",
                        "actor_state": {
                            "kind": "guest",
                            "side": "defender",
                            "percent": 75,
                            "status": "healthy",
                            "status_label": "状态充足",
                        },
                    },
                ],
            }
        ],
    )

    assert client.login(username="battle_report_charging_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    status_start = body.index('class="event-unit-summary event-status-layout"')
    status_end = body.index("</div>", status_start)
    status_markup = body[status_start:status_end]
    assert "蓄势待发的长名门客" in status_markup
    assert 'data-unit-state-side="attacker"' in status_markup
    assert "冲锋中" in status_markup
    assert status_markup.index("event-unit-name") < status_markup.index("battle-unit-state")
    assert status_markup.index("battle-unit-state") < status_markup.index("status-pill")
    status_summaries = BeautifulSoup(body, "html.parser").select(".event-status-layout")
    enemy_summary = next(summary for summary in status_summaries if "敌方冲锋门客" in summary.get_text())
    assert "冲锋中" in enemy_summary.get_text()
    assert enemy_summary.select_one(".battle-unit-state") is None


@pytest.mark.django_db
def test_report_view_renders_passive_event(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="battle_report_passive_user",
        password="pass123",
        email="battle_report_passive_user@test.local",
    )
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[{"name": "甲", "guest_id": 1, "template_key": "a"}],
        defender_team=[{"name": "张无忌", "guest_id": None, "template_key": "arena_gl_top_zhang_wuji_boss"}],
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "type": "passive",
                        "side": "attacker",
                        "order": 1,
                        "unit": "张无忌",
                        "effect": "九阳护体",
                        "healed": 15000,
                    }
                ],
            }
        ],
    )

    assert client.login(username="battle_report_passive_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    body = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "九阳护体" in body
    assert "恢复 +15000 生命" in body
    assert "event-passive-layout" in body
    assert "event-passive-tag" in body
    assert "被动技能：九阳护体" in _normalized_report_text(body)


@pytest.mark.django_db
def test_report_view_renders_passive_hp_loss(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="battle_report_passive_loss_user",
        password="pass123",
        email="battle_report_passive_loss_user@test.local",
    )
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="武痴",
        battle_type="arena_coop",
        attacker_team=[{"name": "武痴", "guest_id": 1, "template_key": "a"}],
        defender_team=[{"name": "乙", "guest_id": None, "template_key": "b"}],
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "type": "passive",
                        "side": "attacker",
                        "order": 1,
                        "unit": "武痴",
                        "effect": "嗜血狂怒",
                        "lost": 90,
                    }
                ],
            }
        ],
    )

    assert client.login(username="battle_report_passive_loss_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    body = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "嗜血狂怒" in body
    assert "损失 90 生命" in body


@pytest.mark.django_db
def test_report_view_renders_attack_embedded_passive_events(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="battle_report_attack_passive_user",
        password="pass123",
        email="battle_report_attack_passive_user@test.local",
    )
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[{"name": "甲", "guest_id": 1, "template_key": "a"}],
        defender_team=[{"name": "张无忌", "guest_id": None, "template_key": "arena_gl_top_zhang_wuji_boss"}],
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "side": "attacker",
                        "order": 1,
                        "actor": "甲",
                        "target": "张无忌",
                        "damage": 1000,
                        "is_crit": False,
                        "is_dodge": False,
                        "skills": [],
                        "agility": 100,
                        "kind": "guest",
                        "priority": 0,
                        "status_inflicted": [],
                        "index": 0,
                        "kills": 0,
                        "target_defeated": False,
                        "passive_events_before": [
                            {
                                "type": "passive",
                                "unit": "甲",
                                "effect": "先手蓄劲",
                                "message": "蓄势待发",
                                "unit_state": {
                                    "side": "attacker",
                                    "percent": 65,
                                    "status": "healthy",
                                    "status_label": "状态充足",
                                },
                            }
                        ],
                        "passive_events_after": [
                            {
                                "type": "passive",
                                "unit": "张无忌",
                                "effect": "乾坤留痕",
                                "message": "卸力反震",
                                "unit_state": {
                                    "side": "defender",
                                    "percent": 40,
                                    "status": "warning",
                                    "status_label": "状态偏低",
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert client.login(username="battle_report_attack_passive_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    body = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "先手蓄劲" in body
    assert "蓄势待发" in body
    assert "乾坤留痕" in body
    assert "卸力反震" in body
    normalized_text = _normalized_report_text(body)
    assert "普通攻击：对张无忌造成伤害1000，伤害人数0" in normalized_text
    assert "被动技能：先手蓄劲" in normalized_text
    assert "被动技能：乾坤留痕" in normalized_text
    assert body.count('data-unit-state-side="attacker"') == 1
    assert 'data-unit-state-side="defender"' not in body


@pytest.mark.django_db
def test_report_view_renders_additional_target_embedded_passive_events(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="battle_report_multi_target_passive_user",
        password="pass123",
        email="battle_report_multi_target_passive_user@test.local",
    )
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[{"name": "甲", "guest_id": 1, "template_key": "a"}],
        defender_team=[{"name": "张无忌", "guest_id": None, "template_key": "arena_gl_top_zhang_wuji_boss"}],
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "side": "attacker",
                        "order": 1,
                        "actor": "甲",
                        "target": "杨逍",
                        "damage": 1000,
                        "is_crit": False,
                        "is_dodge": False,
                        "skills": ["乾坤圣火印"],
                        "agility": 100,
                        "kind": "guest",
                        "priority": 0,
                        "status_inflicted": [],
                        "index": 0,
                        "kills": 0,
                        "target_defeated": False,
                        "additional_targets": [
                            {
                                "actor": "甲",
                                "target": "张无忌",
                                "damage": 800,
                                "is_crit": False,
                                "is_dodge": False,
                                "skills": ["乾坤圣火印"],
                                "agility": 100,
                                "kind": "guest",
                                "priority": 0,
                                "status_inflicted": [],
                                "index": 1,
                                "kills": 0,
                                "target_defeated": False,
                                "reflect_damage": 25,
                                "reflect_kills": 0,
                                "reflect_defeated": False,
                                "actor_state": {
                                    "side": "attacker",
                                    "percent": 45,
                                    "status": "warning",
                                    "status_label": "状态偏低",
                                },
                                "target_state": {
                                    "side": "defender",
                                    "percent": 30,
                                    "status": "warning",
                                    "status_label": "状态偏低",
                                },
                                "passive_events_before": [
                                    {"type": "passive", "unit": "甲", "effect": "先手蓄劲", "message": "追击再起"}
                                ],
                                "passive_events_after": [
                                    {"type": "passive", "unit": "张无忌", "effect": "乾坤留痕", "message": "卸力反震"}
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    )

    assert client.login(username="battle_report_multi_target_passive_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    body = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "先手蓄劲" in body
    assert "追击再起" in body
    assert "乾坤留痕" in body
    assert "卸力反震" in body
    normalized_text = _normalized_report_text(body)
    assert "爆发技能：乾坤圣火印，对杨逍造成伤害1000，伤害人数0" in normalized_text
    assert "波及张无忌，造成伤害800，伤害人数0" in normalized_text
    actor_summary_start = body.index('class="event-unit-summary"')
    actor_summary_end = body.index("</div>", actor_summary_start)
    assert "乾坤圣火印" not in body[actor_summary_start:actor_summary_end]
    settlement_start = body.index('class="event-target-summary event-actor-settlement-summary reflect-text"')
    settlement_end = body.index("</div>", settlement_start)
    settlement_markup = body[settlement_start:settlement_end]
    assert "甲" in settlement_markup
    assert 'data-unit-state-side="attacker"' in settlement_markup
    assert 'data-unit-state-side="defender"' not in body
