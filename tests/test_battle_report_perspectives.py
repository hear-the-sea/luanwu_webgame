from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.urls import reverse

from battle import view_helpers
from gameplay.models import ArenaEntry, ArenaMatch, ArenaTournament
from gameplay.services.manor.core import ensure_manor
from tests.battle_report_view.support import create_report


def _label(side: str, viewer: str) -> str:
    if viewer == "spectator":
        return "进攻方" if side == "attacker" else "防守方"
    return "我方" if side == viewer else "敌方"


def _text(node) -> str:
    return "".join(node.get_text().split())


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("dodge", [False, True])
def test_cavalry_preemptive_report_uses_normal_attack_without_sword_technology(legacy, nested, dodge):
    hit = {
        "actor": "嗜血铁骑",
        "target": "枪王",
        "kind": "troop",
        "side": "attacker",
        "priority": -1,
        "priority_phase": -1,
        "damage": 123,
        "skills": [],
        "is_dodge": dodge,
    }
    if not legacy:
        hit["actor_template_key"] = "bloodthirsty_cavalry"
    if nested:
        hit["additional_attacks"] = [{"actor": "嗜血铁骑", "target": "枪王", "damage": 123, "skills": []}]
    body = render_to_string("battle/partials/round_event_list.html", {"events": [hit], "my_side": "attacker"})
    assert "驭剑之术" not in body
    assert "铁骑冲阵" not in body
    assert "普通攻击" in body


def test_cavalry_charge_report_keeps_active_skill_name():
    hit = {
        "actor": "嗜血铁骑",
        "actor_template_key": "bloodthirsty_cavalry",
        "target": "枪王",
        "kind": "troop",
        "side": "attacker",
        "priority": -1,
        "damage": 123,
        "skills": ["铁骑冲阵"],
    }
    body = render_to_string("battle/partials/round_event_list.html", {"events": [hit], "my_side": "attacker"})
    assert "爆发技能：铁骑冲阵" in body
    assert "驭剑之术" not in body


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("side", ["attacker", "defender"])
@pytest.mark.parametrize("branch", ["normal", "crit", "double", "bow", "sword", "counter", "reflect", "city"])
@pytest.mark.parametrize("nested", [False, True])
def test_attack_and_retaliation_defeat_labels_follow_viewer(viewer, side, branch, nested):
    other = "defender" if side == "attacker" else "attacker"
    hit = {"actor": "刀圣", "target": "拳圣", "kind": "troop", "damage": 123, "kills": 2}
    if branch in {"counter", "reflect"}:
        hit.update({f"{branch}_damage": 45, f"{branch}_kills": 1, f"{branch}_defeated": True})
        hit["target"] = "枪王" if branch == "counter" else "剑圣"
    else:
        hit["target_defeated"] = True
    if branch == "crit":
        hit["is_crit"] = True
    elif branch == "double":
        hit["is_double_strike"] = True
    elif branch == "city":
        hit.update(target="城墙", target_state={"kind": "city_defense", "side": other})
    event = {**hit, "side": side}
    if branch in {"bow", "sword"}:
        event.update(
            actor="箭神" if branch == "bow" else "剑圣",
            priority=-2 if branch == "bow" else -1,
            priority_phase=-2 if branch == "bow" else -1,
        )
    if nested:
        # 旧日志的追加攻击、额外目标可能没有自己的 side 或 priority 字段。
        child = {**hit, "actor": event["actor"]}
        event = {
            **event,
            "target_defeated": False,
            "counter_damage": 0,
            "reflect_damage": 0,
            "additional_attacks": [{**child, "additional_targets": [dict(child)]}],
        }
    document = BeautifulSoup(
        render_to_string(
            "battle/partials/round_event_list.html",
            {"events": [event], "my_side": viewer, "is_spectator": viewer == "spectator"},
        ),
        "html.parser",
    )
    if branch in {"counter", "reflect"}:
        rows = document.select(f".{branch}-text")
        effect = "反戈一击" if branch == "counter" else "护身剑罡"
        assert all(
            _text(node.select_one(".event-effect-source")) == f"{_label(other, viewer)}{hit['target']}爆发{effect}"
            for node in rows
        )
        expected = f"{_label(other, viewer)}{hit['target']}爆发{effect}，造成伤害45，伤害人数1，{_label(side, viewer)}{event['actor']}全军覆没"
    else:
        rows = [
            node
            for node in document.select(".event-target-detail")
            if "全军覆没" in _text(node) or "被摧毁" in _text(node)
        ]
        expected = "城墙被摧毁" if branch == "city" else f"{_label(other, viewer)}{hit['target']}全军覆没"
    assert len(rows) == (2 if nested else 1)
    assert all(_text(node).endswith(expected) for node in rows)
    if viewer == "spectator":
        assert "我方" not in _text(document)
        assert "敌方" not in _text(document)


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
def test_legacy_missing_side_uses_snapshot_or_neutral_target_name(viewer):
    event = {"actor": "刀圣", "target": "拳圣", "kind": "troop", "damage": 10, "target_defeated": True}
    for state, expected in (
        ({}, "拳圣全军覆没"),
        ({"actor_state": {"side": "attacker"}}, f"{_label('defender', viewer)}拳圣全军覆没"),
    ):
        body = render_to_string(
            "battle/partials/round_event_list.html",
            {
                "events": [{**event, **state}],
                "my_side": viewer,
                "is_spectator": viewer == "spectator",
            },
        )
        assert expected in _text(BeautifulSoup(body, "html.parser"))


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("target_side", ["attacker", "defender"])
def test_guest_defeating_troops_names_the_defeated_troop_for_every_viewer(viewer, target_side):
    actor_side = "defender" if target_side == "attacker" else "attacker"
    event = {
        "actor": "吕布",
        "kind": "guest",
        "side": actor_side,
        "target": "刀霸",
        "target_state": {"kind": "troop", "side": target_side},
        "target_defeated": True,
        "damage": 123,
    }
    html = render_to_string(
        "battle/partials/round_event_list.html",
        {
            "events": [event],
            "my_side": viewer,
            "is_spectator": viewer == "spectator",
        },
    )
    text = _text(BeautifulSoup(html, "html.parser"))
    assert f"{_label(target_side, viewer)}刀霸全军覆没" in text
    assert f"{_label(target_side, viewer)}刀霸刀霸" not in text


def test_legacy_round_groups_snapshot_sides_and_preserves_unknown_events():
    attack = {"actor": "刀圣", "actor_state": {"side": "attacker"}, "damage": 10}
    heal = {"type": "heal", "unit": "拳圣", "unit_state": {"side": "defender"}, "healed": 10}
    unknown = {"actor": "旧日志门客", "damage": 20}
    raw = {"round": 1, "events": [attack, heal, unknown]}
    row = view_helpers.build_rounds_display([raw], {})[0]
    assert row["attacker_events"] == [attack]
    assert row["defender_events"] == [heal]
    assert row["unassigned_events"] == [unknown]
    assert all("side" not in event for event in raw["events"])


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("side", ["attacker", "defender"])
@pytest.mark.parametrize("branch", ["normal", "counter", "reflect"])
def test_guest_defeat_does_not_claim_whole_army_was_defeated(viewer, side, branch):
    other = "defender" if side == "attacker" else "attacker"
    event = {
        "side": side,
        "actor": "吕布",
        "target": "赵云",
        "kind": "guest",
        "damage": 123,
        "target_state": {"kind": "guest", "side": other},
    }
    if branch == "normal":
        event["target_defeated"] = True
        expected = f"{_label(other, viewer)}赵云被击败"
    else:
        event.update({f"{branch}_damage": 20, f"{branch}_defeated": True})
        expected = f"{_label(side, viewer)}吕布被击败"
    body = render_to_string(
        "battle/partials/round_event_list.html",
        {
            "events": [event],
            "my_side": viewer,
            "is_spectator": viewer == "spectator",
        },
    )
    text = _text(BeautifulSoup(body, "html.parser"))
    assert expected in text
    assert "全军覆没" not in text


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("side", ["attacker", "defender"])
@pytest.mark.parametrize("heal_strength", [12, 0, None])
def test_fist_heal_is_compact_and_preserves_viewer_visibility(viewer, side, heal_strength):
    event = {
        "type": "heal",
        "side": side,
        "order": 1,
        "unit": "拳圣",
        "effect": "五气朝元",
        "healed": 120,
        "unit_state": {"kind": "troop", "side": side, "percent": 80, "status": "healthy"},
    }
    if heal_strength is not None:
        event["heal_strength"] = heal_strength
    document = BeautifulSoup(
        render_to_string(
            "battle/partials/round_event_list.html",
            {"events": [event], "my_side": viewer, "is_spectator": viewer == "spectator"},
        ),
        "html.parser",
    )
    summary = document.select_one(".event-heal-summary")
    assert summary is not None
    expected = "拳圣五气朝元恢复生命120"
    if heal_strength is not None:
        expected += f"恢复人数{heal_strength}"
    assert _text(summary) == expected
    assert not summary.select(".event-unit-summary, .event-unit-name, .event-unit-skills")
    bars = summary.select(".battle-unit-state")
    assert len(bars) == (1 if viewer == side else 0)
    if bars:
        assert bars[0]["data-unit-state-side"] == viewer
        assert summary.find_all(recursive=False)[-1] == bars[0]


@pytest.mark.parametrize("battle_type", ["raid", "arena"])
@pytest.mark.parametrize("viewer_id,expected", [(50, "attacker"), (60, "defender")])
def test_historical_pvp_viewer_side_does_not_change_after_capture(monkeypatch, battle_type, viewer_id, expected):
    for name in (
        "_load_mission_run_for_report",
        "_load_raid_run_for_report",
        "_load_arena_match_for_report",
        "_load_arena_coop_event_for_report",
        "_load_guild_mission_run_for_report",
        "_load_guild_raid_run_for_report",
    ):
        monkeypatch.setattr(view_helpers, name, lambda _report: None)
    infer = Mock(return_value="defender" if expected == "attacker" else "attacker")
    monkeypatch.setattr(view_helpers, "infer_side_from_guest_ownership", infer)
    messages = Mock()
    messages.filter.return_value.exists.return_value = viewer_id == 60
    report = SimpleNamespace(battle_type=battle_type, manor_id=50, messages=messages)
    assert view_helpers.resolve_report_runtime_context(report, manor_id=viewer_id)["player_side"] == expected
    infer.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("winner", ["attacker", "defender", "draw"])
def test_public_arena_page_for_all_viewers_and_results(client, django_user_model, winner):
    users = {
        side: django_user_model.objects.create_user(username=f"perspective_{winner}_{side}", password="pass")
        for side in ("attacker", "defender", "spectator")
    }
    manors = {side: ensure_manor(user) for side, user in users.items()}
    events = []
    for side in ("attacker", "defender"):
        other = "defender" if side == "attacker" else "attacker"
        events.append(
            {
                "side": side,
                "order": len(events) + 1,
                "actor": "刀圣",
                "target": "拳圣",
                "kind": "troop",
                "damage": 123,
                "kills": 2,
                "target_defeated": True,
                "is_double_strike": True,
                "actor_state": {"side": side, "status": "healthy", "percent": 80, "status_label": "状态充足"},
                "target_state": {
                    "side": other,
                    "kind": "troop",
                    "status": "critical",
                    "percent": 0,
                    "status_label": "已阵亡",
                },
            }
        )
    events.append({"actor": "旧日志门客", "target": "拳圣", "kind": "guest", "damage": 10})

    def equipment(name):
        return [
            {
                "name": name,
                "equipped_count": 1,
                "effective_count": 1,
                "bonuses": {"gong": {"attack": {"flat": 0, "pct": 0.01}}},
            }
        ]

    report = create_report(
        manor=manors["attacker"],
        opponent_name=manors["defender"].display_name,
        battle_type="arena",
        winner=winner,
        rounds=[{"round": 1, "events": events}],
        attacker_equipment_bonuses=equipment("进攻机关"),
        defender_equipment_bonuses=equipment("防守机关"),
    )
    report.losses = {
        "attacker": {"casualties": [{"label": "进攻损失单位", "lost": 2}]},
        "defender": {"casualties": [{"label": "防守损失单位", "lost": 3}]},
    }
    report.save(update_fields=["losses"])
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        started_at=report.starts_at,
        next_round_at=report.starts_at,
    )
    entries = {
        side: ArenaEntry.objects.create(tournament=tournament, manor=manors[side]) for side in ("attacker", "defender")
    }
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=entries["attacker"],
        defender_entry=entries["defender"],
        winner_entry=entries.get(winner),
        status=ArenaMatch.Status.COMPLETED,
        battle_report=report,
        resolved_at=report.completed_at,
    )
    for viewer, user in users.items():
        client.force_login(user)
        response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
        assert response.status_code == 200
        assert response.context["player_side"] == viewer
        doc = BeautifulSoup(response.content, "html.parser")
        assert "旧日志门客" in _text(doc.select_one(".round-action-cell.unassigned"))
        bars = doc.select(".battle-unit-state")
        if viewer == "spectator":
            assert not bars
            assert "我方" not in _text(doc.select_one(".battle-report-page"))
            assert "敌方" not in _text(doc.select_one(".battle-report-page"))
        else:
            assert len(bars) == 1
            assert all(node["data-unit-state-side"] == viewer for node in bars)
        assert response.context["equipment_left_bonuses"][0]["name"] == "进攻机关"
        assert response.context["equipment_right_bonuses"][0]["name"] == "防守机关"
        assert response.context["loss_left_casualties"][0]["label"] == "进攻损失单位"
        assert response.context["loss_right_casualties"][0]["label"] == "防守损失单位"
        if winner == "draw":
            assert "不分胜负" in _text(doc.select_one(".battle-result"))
            assert "战斗失败" not in _text(doc.select_one(".battle-settlement-card"))
        elif viewer == "spectator":
            assert f"{'进攻方' if winner == 'attacker' else '防守方'}胜利" in _text(doc.select_one(".battle-result"))
        else:
            assert response.context["player_won"] == (viewer == winner)
