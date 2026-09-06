from __future__ import annotations

import pytest
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor
from tests.battle_report_view.support import create_report


@pytest.mark.django_db
def test_report_round_renders_starting_lineups_in_fixed_attacker_defender_columns(
    client,
    django_user_model,
    monkeypatch,
):
    user = django_user_model.objects.create_user(username="round_lineup_report", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="演武对手",
        battle_type="task1",
        rounds=[
            {
                "round": 2,
                "lineups": {
                    "attacker": {
                        "guests": [
                            {
                                "name": "进攻慢门客",
                                "template_key": "attacker_round_guest",
                                "agility": 80,
                                "current_hp": 900,
                                "max_hp": 1000,
                            },
                            {
                                "name": "进攻快门客",
                                "template_key": "attacker_fast_round_guest",
                                "agility": 180,
                                "current_hp": 850,
                                "max_hp": 1000,
                            },
                        ],
                        "city_defenses": [
                            {
                                "key": "wall",
                                "name": "城墙",
                                "level": 8,
                                "hp": 24000,
                                "max_hp": 24000,
                            }
                        ],
                        "troops": [
                            {"name": "刀圣", "template_key": "dao_sheng", "count": 500},
                            {"name": "剑圣", "template_key": "jian_sheng", "count": 450},
                        ],
                    },
                    "defender": {
                        "guests": [
                            {
                                "name": "防守慢门客",
                                "template_key": "defender_round_guest",
                                "agility": 90,
                                "current_hp": 700,
                                "max_hp": 1000,
                            },
                            {
                                "name": "防守快门客",
                                "template_key": "defender_fast_round_guest",
                                "agility": 210,
                                "current_hp": 650,
                                "max_hp": 1000,
                            },
                        ],
                        "troops": [],
                    },
                },
                "events": [
                    {
                        "side": "attacker",
                        "order": 1,
                        "actor": "进攻慢门客",
                        "status": "charging",
                        "message": "蓄势待发",
                    },
                    {
                        "side": "defender",
                        "order": 2,
                        "actor": "防守慢门客",
                        "status": "charging",
                        "message": "严阵以待",
                    },
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "battle.views.load_avatar_map",
        lambda _keys: {
            "attacker_round_guest": "/media/guests/attacker.webp",
            "attacker_fast_round_guest": "/media/guests/attacker-fast.webp",
            "defender_round_guest": "/media/guests/defender.webp",
            "defender_fast_round_guest": "/media/guests/defender-fast.webp",
        },
    )

    assert client.login(username="round_lineup_report", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    battle_round = response.context["battle_rounds"][0]
    assert [event["actor"] for event in battle_round["attacker_events"]] == ["进攻慢门客"]
    assert [event["actor"] for event in battle_round["defender_events"]] == ["防守慢门客"]

    body = response.content.decode("utf-8")
    assert '<section class="dashboard battle-report-page">' in body
    round_html = body[
        body.index('data-battle-round="2"') : body.index("</article>", body.index('data-battle-round="2"'))
    ]
    assert round_html.index("第 2 回合") < round_html.index("进攻方")
    assert round_html.index("进攻方") < round_html.index('aria-label="进攻方剩余门客"')
    assert round_html.index('aria-label="进攻方剩余门客"') < round_html.index('aria-label="进攻方剩余护院"')
    assert round_html.index('aria-label="进攻方剩余护院"') < round_html.index('aria-label="进攻方行动记录"')
    assert 'src="/media/guests/attacker.webp" alt="进攻慢门客"' in round_html
    assert 'src="/media/guests/attacker-fast.webp" alt="进攻快门客"' in round_html
    assert 'src="/media/guests/defender.webp" alt="防守慢门客"' in round_html
    assert 'src="/media/guests/defender-fast.webp" alt="防守快门客"' in round_html
    assert round_html.index('alt="进攻快门客"') < round_html.index('alt="进攻慢门客"')
    assert round_html.index('alt="防守快门客"') < round_html.index('alt="防守慢门客"')
    assert round_html.index("崭新的高级城墙") < round_html.index("刀圣</span>:<strong>500</strong>")
    assert "刀圣</span>:<strong>500</strong>" in round_html
    assert "剑圣</span>:<strong>450</strong>" in round_html
    assert "被歼灭" in round_html
    assert "蓄势待发" in round_html
    assert "严阵以待" in round_html
    assert "回合开始阵容" not in round_html
    assert "具体行动" not in round_html
    assert "旧战报未记录回合阵容" not in round_html


@pytest.mark.django_db
def test_report_round_marks_missing_historical_lineup_snapshot(client, django_user_model):
    user = django_user_model.objects.create_user(username="legacy_round_lineup_report", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="旧战报对手",
        battle_type="task1",
        rounds=[{"round": 1, "events": []}],
    )

    assert client.login(username="legacy_round_lineup_report", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["battle_rounds"][0]["has_lineup_snapshot"] is False
    assert "旧战报未记录回合阵容" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_report_uses_reserved_equipment_and_settlement_tables_without_lineup_comparison(client, django_user_model):
    user = django_user_model.objects.create_user(username="flat_report_tables", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="演武对手",
        battle_type="task1",
        attacker_team=[
            {
                "name": "赵云",
                "guest_id": 31,
                "template_key": "zhao_yun",
            }
        ],
        defender_team=[{"name": "守将", "guest_id": 32, "template_key": "defender"}],
    )
    report.losses = {
        "attacker": {
            "troops_lost": 5,
            "casualties": [{"label": "刀圣", "lost": 5}],
        },
        "defender": {
            "troops_lost": 3,
            "casualties": [{"label": "剑圣", "lost": 3}],
        },
    }
    report.drops = {"silver": 88}
    report.save(update_fields=["losses", "drops"])

    assert client.login(username="flat_report_tables", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert '<div class="tw-section-header battle-report-title">' in body
    assert "<h1>= 演武对手 战报 =</h1>" in body
    assert "阵容对比" not in body
    assert 'class="battle-overview-table"' in body
    assert body.count('<span class="battle-table-label">装备属性加成</span>') == 2
    assert body.count('<span class="battle-table-empty">无</span>') == 2
    assert response.context["attacker_equipment_bonuses"] == []
    assert response.context["defender_equipment_bonuses"] == []
    assert 'class="battle-settlement-table"' in body
    assert "刀圣 × 5" in body
    assert "剑圣 × 3" in body
    assert ">5</strong>" not in body
    assert ">3</strong>" not in body
    assert "银两 +88" in body


@pytest.mark.django_db
def test_report_renders_guest_only_casualties_as_units(client, django_user_model):
    user = django_user_model.objects.create_user(username="guest_only_casualties", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="门客演武对手",
        battle_type="task1",
    )
    report.losses = {
        "attacker": {
            "troops_lost": 0,
            "casualties": [
                {"key": "attacker_guest", "label": "阵亡门客甲", "lost": 1},
                {"key": "zero_loss_troop", "label": "不应显示的零损失单位", "lost": 0},
                {"key": "invalid_loss", "label": "不应显示的非法损失", "lost": "invalid"},
            ],
        },
        "defender": {
            "troops_lost": 0,
            "casualties": [{"key": "defender_guest", "label": "阵亡门客乙", "lost": "2"}],
        },
    }
    report.save(update_fields=["losses"])

    assert client.login(username="guest_only_casualties", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["loss_left_casualties"] == [{"label": "阵亡门客甲", "lost": 1}]
    assert response.context["loss_right_casualties"] == [{"label": "阵亡门客乙", "lost": 2}]

    settlement_html = response.content.decode("utf-8").split('<div class="battle-settlement-table"', 1)[1]
    assert settlement_html.count("阵亡单位") == 2
    assert "阵亡门客甲 × 1" in settlement_html
    assert "阵亡门客乙 × 2" in settlement_html
    assert "不应显示的零损失单位" not in settlement_html
    assert "不应显示的非法损失" not in settlement_html


@pytest.mark.django_db
def test_report_renders_persisted_troop_device_bonuses_and_five_copy_cap(client, django_user_model):
    user = django_user_model.objects.create_user(username="device_bonus_report", password="pass123")
    manor = ensure_manor(user)
    report = create_report(
        manor=manor,
        opponent_name="器械演武",
        battle_type="task1",
        attacker_equipment_bonuses=[
            {
                "template_key": "equip_jixiemao",
                "name": "机械猫",
                "equipped_count": 7,
                "effective_count": 5,
                "capped": True,
                "bonuses": {"gong": {"hp": {"flat": 0, "pct": 0.05}}},
            }
        ],
        defender_equipment_bonuses=[
            {
                "template_key": "equip_xuanwujigui",
                "name": "玄武机龟",
                "equipped_count": 2,
                "effective_count": 2,
                "capped": False,
                "bonuses": {
                    "qiang": {
                        "attack": {"flat": 0, "pct": 0.01},
                        "defense": {"flat": 0, "pct": 0.01},
                        "hp": {"flat": 0, "pct": 0.01},
                    }
                },
            }
        ],
    )

    assert client.login(username="device_bonus_report", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "机械猫 × 5" in body
    assert "弓系生命 +5%" in body
    assert "已装备 7 件，仅前 5 件生效" in body
    assert "玄武机龟 × 2" in body
    assert "枪系全部属性 +1%" in body
