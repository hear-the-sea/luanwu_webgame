from __future__ import annotations

import re
from copy import deepcopy
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.template import Context, Engine
from django.test import RequestFactory, override_settings
from django.utils.html import strip_tags

from battle_debugger.config import BattleConfig, ConfigLoader, InvalidPresetError, PartyConfig
from battle_debugger.management.commands.battle_debug import Command as BattleDebugCommand
from battle_debugger.simulator import BattleSimulator, patch_battle_params
from battle_debugger.views import custom_config, result_detail, simulate, tune

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _make_staff_post_request(django_user_model, path: str, data: dict):
    user = django_user_model.objects.create_user(
        username=f"debugger-{path.replace('/', '-')}",
        password="pass",
        is_staff=True,
    )
    request = RequestFactory().post(path, data)
    request.user = user
    return request


def _valid_config() -> BattleConfig:
    return BattleConfig(
        name="test",
        attacker=PartyConfig(troops={"attacker_troop": 1}),
        defender=PartyConfig(troops={"defender_troop": 1}),
    )


@override_settings(DEBUG=True)
def test_load_preset_invalid_name_raises_explicit_error():
    loader = ConfigLoader()

    with pytest.raises(InvalidPresetError, match="预设名称无效"):
        loader.load_preset("../bad")


def test_battle_debugger_defense_override_preserves_fractional_value():
    import battle.combat_math as combat_math

    troop = SimpleNamespace(
        kind="troop",
        unit_defense=2.5,
        troop_strength=9,
        initial_troop_strength=9,
        defense=22.5,
    )

    with patch_battle_params({"troop_defense_divisor": 2.0}):
        assert combat_math.effective_defense_value(troop) == pytest.approx(3.75)


def test_battle_debugger_guest_attack_preserves_fractional_weighted_value():
    guest = SimpleNamespace(force=1, intellect=2)

    attack = BattleSimulator(_valid_config())._calculate_attack(guest, "military")

    assert attack == pytest.approx(1.3)


def test_debugger_web_report_shows_followup_attack_dodge_and_status():
    event = {
        "side": "attacker",
        "actor": "箭神",
        "kind": "troop",
        "target": "枪兵",
        "damage": 6000,
        "kills": 6,
        "additional_attacks": [
            {"target": "盾兵", "damage": 0, "kills": 0, "is_dodge": True},
            {
                "target": "刀兵",
                "damage": 5000,
                "kills": 5,
                "status_inflicted": ["流血"],
                "additional_targets": [{"target": "援军", "damage": 100, "kills": 1}],
            },
        ],
    }

    template_engine = Engine(
        dirs=[str(PROJECT_ROOT / "battle_debugger" / "templates"), str(PROJECT_ROOT / "battle" / "templates")],
        libraries={"battle_report": "battle.templatetags.battle_report"},
    )
    html = template_engine.get_template("battle_debugger/partials/event_list.html").render(
        Context({"round": {"events": [event]}, "side": "attacker", "side_name": "attacker"})
    )
    report_text = "".join(strip_tags(html).split())

    assert "普通攻击：对枪兵造成伤害6000" in report_text
    assert "普通攻击：盾兵闪避了攻击" in report_text
    assert "普通攻击：对刀兵造成伤害5000" in report_text
    assert "波及援军，造成伤害100，伤害人数1" in report_text
    assert "造成状态：流血" in report_text
    assert "盾兵造成伤害0" not in report_text


def test_debugger_command_shows_each_troop_attack():
    command = BattleDebugCommand()
    output = StringIO()
    command.stdout = output
    command.print_combat_log(
        [
            {
                "round": 1,
                "events": [
                    {
                        "actor": "箭神",
                        "kind": "troop",
                        "target": "枪兵",
                        "damage": 6000,
                        "kills": 6,
                        "additional_attacks": [
                            {"target": "盾兵", "is_dodge": True},
                            {"target": "刀兵", "damage": 5000, "kills": 5},
                        ],
                    }
                ],
            }
        ]
    )

    report_text = output.getvalue()
    assert "普通攻击：对枪兵造成伤害6000" in report_text
    assert "普通攻击：盾兵闪避了攻击" in report_text
    assert "普通攻击：对刀兵造成伤害5000" in report_text


@pytest.mark.parametrize("snapshot_side", [None, "attacker", "defender"])
def test_debugger_command_legacy_retaliation_uses_snapshot_or_neutral_side(snapshot_side):
    event = {
        "actor": "刀圣",
        "target": "枪王",
        "kind": "troop",
        "damage": 100,
        "counter_damage": 20,
        "counter_kills": 1,
        "counter_defeated": True,
    }
    if snapshot_side:
        event["actor_state"] = {"side": snapshot_side}
    command = BattleDebugCommand()
    output = StringIO()
    command.stdout = output
    command.print_combat_log([{"round": 1, "events": [event]}])
    text = "".join(output.getvalue().split())
    if snapshot_side is None:
        expected = "枪王爆发反戈一击，造成伤害20，伤害人数1，刀圣全军覆没"
        assert "进攻方" not in text and "防守方" not in text
    else:
        source = "防守方" if snapshot_side == "attacker" else "进攻方"
        victim = "进攻方" if snapshot_side == "attacker" else "防守方"
        expected = f"{source}枪王爆发反戈一击，造成伤害20，伤害人数1，{victim}全军覆没"
    assert expected in text


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_simulate_renders_error_for_invalid_preset_name(django_user_model, monkeypatch):
    request = _make_staff_post_request(
        django_user_model,
        "/debugger/simulate/",
        {"preset": "../bad", "repeat": "1"},
    )

    def _broken_load_preset(_self, _preset_name):
        raise InvalidPresetError("预设名称无效: '../bad'")

    monkeypatch.setattr("battle_debugger.views.ConfigLoader.load_preset", _broken_load_preset)
    monkeypatch.setattr("battle_debugger.views._render_debugger_error", lambda _request, message: HttpResponse(message))

    response = simulate(request)

    assert response.status_code == 200
    assert "预设名称无效".encode("utf-8") in response.content


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_simulate_bubbles_programming_errors(django_user_model, monkeypatch):
    request = _make_staff_post_request(
        django_user_model,
        "/debugger/simulate/",
        {"preset": "valid", "repeat": "1"},
    )

    monkeypatch.setattr("battle_debugger.views.ConfigLoader.load_preset", lambda _self, _preset_name: _valid_config())

    class BrokenSimulator:
        def __init__(self, _config):
            pass

        def run_battle(self, seed=None):
            raise AssertionError("broken battle debugger simulate contract")

    monkeypatch.setattr("battle_debugger.views.BattleSimulator", BrokenSimulator)

    with pytest.raises(AssertionError, match="broken battle debugger simulate contract"):
        simulate(request)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_tune_bubbles_programming_errors(django_user_model, monkeypatch):
    request = _make_staff_post_request(
        django_user_model,
        "/debugger/tune/",
        {
            "preset": "valid",
            "param": "slaughter_multiplier",
            "values": "10,20",
            "repeat": "1",
        },
    )

    monkeypatch.setattr("battle_debugger.views.ConfigLoader.load_preset", lambda _self, _preset_name: _valid_config())

    class BrokenSimulator:
        def __init__(self, _config):
            pass

        def run_battle(self, seed=None):
            raise AssertionError("broken battle debugger tune contract")

    monkeypatch.setattr("battle_debugger.views.BattleSimulator", BrokenSimulator)

    with pytest.raises(AssertionError, match="broken battle debugger tune contract"):
        tune(request)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_custom_config_bubbles_programming_errors(django_user_model, monkeypatch):
    request = _make_staff_post_request(
        django_user_model,
        "/debugger/custom/",
        {
            "attacker_guest_count": "0",
            "defender_guest_count": "0",
            "attacker_troop_types": ["infantry"],
            "attacker_troop_infantry": "5",
            "defender_troop_types": ["infantry"],
            "defender_troop_infantry": "5",
            "repeat": "1",
        },
    )

    class BrokenSimulator:
        def __init__(self, _config):
            pass

        def run_battle(self, seed=None):
            raise AssertionError("broken battle debugger custom config contract")

    monkeypatch.setattr("battle_debugger.views.BattleSimulator", BrokenSimulator)

    with pytest.raises(AssertionError, match="broken battle debugger custom config contract"):
        custom_config(request)


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_custom_config_renders_error_for_empty_sides(django_user_model, monkeypatch):
    request = _make_staff_post_request(
        django_user_model,
        "/debugger/custom/",
        {
            "attacker_guest_count": "0",
            "defender_guest_count": "0",
            "repeat": "1",
        },
    )

    monkeypatch.setattr("battle_debugger.views._render_debugger_error", lambda _request, message: HttpResponse(message))
    response = custom_config(request)

    assert response.status_code == 200
    assert "攻方必须至少有门客或小兵".encode("utf-8") in response.content


def test_custom_config_template_keeps_page_styles_out_of_main_template():
    template_path = PROJECT_ROOT / "battle_debugger" / "templates" / "battle_debugger" / "custom_config.html"
    template = template_path.read_text(encoding="utf-8")

    assert len(template.splitlines()) <= 500
    assert "<style>" not in template
    assert "css/battle-debugger-custom-config.css" in template


def test_custom_config_template_avoids_dom_xss_sinks():
    template_path = PROJECT_ROOT / "battle_debugger" / "templates" / "battle_debugger" / "custom_config.html"
    template = template_path.read_text(encoding="utf-8")

    assert ".innerHTML" not in template
    assert ".outerHTML" not in template
    assert ".insertAdjacentHTML" not in template
    assert not re.search(r"\son[a-z]+\s*=", template)


def test_custom_config_template_keeps_dynamic_form_contracts():
    template_path = PROJECT_ROOT / "battle_debugger" / "templates" / "battle_debugger" / "custom_config.html"
    template = template_path.read_text(encoding="utf-8")

    assert "document.getElementById(`${side}_guest_count`).value = guestCounters[side]" in template
    assert "name: `${side}_guest_${idx}_template`" in template
    assert "name: `${side}_guest_${idx}_level`" in template
    assert "name: `${side}_guest_${idx}_skills`" in template
    assert "appendHiddenInput(form, `${side}_troop_types`, troopType)" in template
    assert "appendHiddenInput(form, `${side}_troop_${troopType}`, count)" in template


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_result_detail_renders_passive_events_in_detailed_log(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(
        username="debugger-result-detail-passive",
        password="pass",
        is_staff=True,
    )
    request = RequestFactory().get("/debugger/result/passive-check/")
    request.user = user

    result_id = "passive-check"
    cache.set(
        f"battle_result_{result_id}",
        {
            "config": {"name": "passive-check"},
            "results": [
                {
                    "winner": "attacker",
                    "combat_log": [
                        {
                            "round": 1,
                            "events": [
                                {
                                    "type": "passive",
                                    "side": "attacker",
                                    "order": 1,
                                    "unit": "张无忌",
                                    "effect": "九阳护体",
                                    "message": "内息流转",
                                    "healed": 15000,
                                },
                                {
                                    "type": "passive",
                                    "side": "attacker",
                                    "order": 2,
                                    "unit": "武痴",
                                    "effect": "嗜血狂怒",
                                    "lost": 90,
                                },
                                {
                                    "side": "attacker",
                                    "order": 3,
                                    "actor": "甲",
                                    "target": "杨逍",
                                    "damage": 1000,
                                    "skills": ["乾坤圣火印"],
                                    "is_crit": False,
                                    "is_dodge": False,
                                    "agility": 100,
                                    "kind": "guest",
                                    "priority": 0,
                                    "status_inflicted": [],
                                    "kills": 0,
                                    "target_defeated": False,
                                    "additional_targets": [
                                        {
                                            "actor": "甲",
                                            "target": "张无忌",
                                            "damage": 800,
                                            "skills": ["乾坤圣火印"],
                                            "is_crit": False,
                                            "is_dodge": False,
                                            "agility": 100,
                                            "kind": "guest",
                                            "priority": 0,
                                            "status_inflicted": [],
                                            "kills": 0,
                                            "target_defeated": False,
                                            "passive_events_before": [
                                                {
                                                    "type": "passive",
                                                    "unit": "甲",
                                                    "effect": "先手蓄劲",
                                                    "message": "追击再起",
                                                }
                                            ],
                                            "passive_events_after": [
                                                {
                                                    "type": "passive",
                                                    "unit": "张无忌",
                                                    "effect": "乾坤留痕",
                                                    "message": "卸力反震",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ],
        },
        timeout=60,
    )

    templates = deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [*templates[0].get("DIRS", []), str(settings.BASE_DIR / "battle_debugger" / "templates")]
    monkeypatch.setattr("django.urls.reverse", lambda *args, **kwargs: "/debugger/")
    monkeypatch.setattr("django.urls.base.reverse", lambda *args, **kwargs: "/debugger/")

    with override_settings(TEMPLATES=templates):
        response = result_detail(request, result_id=result_id)

    body = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "九阳护体" in body
    assert "内息流转" in body
    assert "嗜血狂怒" in body
    assert "损失 90 生命" in body
    assert "先手蓄劲" in body
    assert "追击再起" in body
    assert "乾坤留痕" in body
    assert "卸力反震" in body
    assert "event-passive-layout" in body
    assert "event-passive-tag" in body
