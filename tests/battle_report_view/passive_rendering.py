from __future__ import annotations

import pytest
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor
from tests.battle_report_view.support import create_report


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
    assert "恢复 +15000 HP" in body
    assert "event-passive-layout" in body
    assert "event-passive-tag" in body


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
    assert "损失 90 HP" in body


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
                            {"type": "passive", "unit": "甲", "effect": "先手蓄劲", "message": "蓄势待发"}
                        ],
                        "passive_events_after": [
                            {"type": "passive", "unit": "张无忌", "effect": "乾坤留痕", "message": "卸力反震"}
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
