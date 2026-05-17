from __future__ import annotations

from copy import deepcopy

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from battle_debugger.config import BattleConfig, ConfigLoader, InvalidPresetError, PartyConfig
from battle_debugger.views import custom_config, result_detail, simulate, tune


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
    assert "损失 90 HP" in body
    assert "先手蓄劲" in body
    assert "追击再起" in body
    assert "乾坤留痕" in body
    assert "卸力反震" in body
    assert "event-passive-layout" in body
    assert "event-passive-tag" in body
