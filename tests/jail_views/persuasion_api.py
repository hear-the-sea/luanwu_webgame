from __future__ import annotations

import json

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from gameplay.models import JailInteractionLog
from gameplay.services import jail as jail_service
from gameplay.services.jail_persuasion.interactions import observe_prisoner

pytestmark = pytest.mark.django_db

FORBIDDEN_PUBLIC_KEYS = {
    "effect_range",
    "prisoner_base_value",
    "ratio",
    "risk",
    "risk_label",
    "success_percent",
    "probability",
    "roll",
    "audit",
}


def _assert_no_forbidden_public_keys(value, *, path="payload"):
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PUBLIC_KEYS.intersection(value)
        assert not forbidden, f"{path} exposes forbidden keys: {sorted(forbidden)}"
        for key, item in value.items():
            _assert_no_forbidden_public_keys(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_forbidden_public_keys(item, path=f"{path}[{index}]")


def _login(client, world):
    client.force_login(world.captor.user)


class _FixedRecruitmentRng:
    def __init__(self, roll):
        self.roll = roll
        self.randint_calls = 0

    def randint(self, start, end):
        self.randint_calls += 1
        if self.randint_calls == 1:
            assert (start, end) == (1, 100)
            return self.roll
        if start <= 0 <= end:
            return 0
        return start

    def choice(self, values):
        return values[0]


def _use_recruitment_roll(monkeypatch, roll):
    captured_results = []

    def recruit_with_fixed_rng(manor, prisoner_id, *, mode="standard"):
        result = jail_service.recruit_prisoner(
            manor,
            prisoner_id,
            mode=mode,
            rng=_FixedRecruitmentRng(roll),
        )
        captured_results.append(result)
        return result

    monkeypatch.setattr("gameplay.views.jail.recruit_prisoner", recruit_with_fixed_rng)
    return captured_results


def _create_recruitment_attempt(prisoner, *, usage_date):
    return JailInteractionLog.objects.create(
        prisoner=prisoner,
        captor=prisoner.captor,
        method="recruitment",
        usage_date=usage_date,
        attempt_scope="recruitment",
        heart_before=prisoner.loyalty,
        heart_after=prisoner.loyalty,
        affinity_before=prisoner.affinity,
        affinity_after=prisoner.affinity,
        outcome=JailInteractionLog.Outcome.NEUTRAL,
        copy_key="feedback.reason.neutral.1",
    )


def test_observe_prisoner_api_returns_initialized_read_state(client, persuasion_world):
    _login(client, persuasion_world)
    response = client.post(
        reverse("gameplay:observe_prisoner_api", kwargs={"prisoner_id": persuasion_world.prisoner.id})
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["prisoner"]["observed"] is True
    assert payload["prisoner"]["revealed_level"] == 1
    assert len(payload["prisoner"]["clues"]) == 1
    _assert_no_forbidden_public_keys(payload)


def test_interact_api_returns_backfire_as_success_result(client, persuasion_world, monkeypatch):
    _login(client, persuasion_world)
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    persuasion_world.prisoner.affinity = 10
    persuasion_world.prisoner.save(update_fields=["affinity"])
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    response = client.post(
        reverse("gameplay:interact_prisoner_api", kwargs={"prisoner_id": persuasion_world.prisoner.id}),
        data=json.dumps({"method": "reason", "speaker_id": persuasion_world.weak_civil.id}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["result"]["outcome"] == "backfire"
    assert payload["result"]["speaker_loyalty_delta"] == -1
    assert payload["result"]["speaker_loyalty"] == 69
    assert payload["result"]["copy_params"]["speaker_name"] == persuasion_world.weak_civil.display_name
    assert payload["result"]["text"]
    _assert_no_forbidden_public_keys(payload)


def test_interact_api_rejects_invalid_method_as_business_error(client, persuasion_world):
    _login(client, persuasion_world)
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    response = client.post(
        reverse("gameplay:interact_prisoner_api", kwargs={"prisoner_id": persuasion_world.prisoner.id}),
        data=json.dumps({"method": "unknown"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "未知的招降手段" in payload["error"]
    _assert_no_forbidden_public_keys(payload)


def test_milestone_api_resolves_choice_and_returns_next_state(client, persuasion_world):
    _login(client, persuasion_world)
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    persuasion_world.prisoner.affinity = 35
    persuasion_world.prisoner.save(update_fields=["affinity"])

    response = client.post(
        reverse("gameplay:resolve_jail_milestone_api", kwargs={"prisoner_id": persuasion_world.prisoner.id}),
        data=json.dumps({"choice": "aligned"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["outcome"] == "event"
    assert payload["result"]["copy_params"]["prisoner_name"] == persuasion_world.prisoner.display_name
    assert payload["prisoner"]["revealed_level"] == 3
    _assert_no_forbidden_public_keys(payload)


def test_recruit_api_returns_successful_public_result(client, persuasion_world, monkeypatch):
    _login(client, persuasion_world)
    captured_results = _use_recruitment_roll(monkeypatch, 1)
    persuasion_world.captor.guests.all().delete()
    prisoner = persuasion_world.prisoner
    prisoner.loyalty = 45
    prisoner.affinity = 60
    prisoner.milestone_stage = 1
    prisoner.save(update_fields=["loyalty", "affinity", "milestone_stage"])

    response = client.post(
        reverse("gameplay:recruit_prisoner_api", kwargs={"prisoner_id": prisoner.id}),
        data=json.dumps({"mode": "negotiated"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    result = captured_results[0]
    assert payload["success"] is True
    assert payload["recruited"] is True
    assert payload["guest_id"] == result.guest.id
    assert payload["mode"] == "negotiated"
    assert payload["initial_loyalty"] == 50
    assert payload["gold_cost"] == 1
    assert payload["copy_key"] == result.copy_key
    assert payload["copy_params"] == {
        "prisoner_name": prisoner.display_name,
        "new_loyalty": 50,
    }
    assert payload["text"]
    _assert_no_forbidden_public_keys(payload)


def test_recruit_api_returns_failed_attempt_as_successful_public_result(client, persuasion_world, monkeypatch):
    _login(client, persuasion_world)
    captured_results = _use_recruitment_roll(monkeypatch, 100)
    persuasion_world.captor.guests.all().delete()
    prisoner = persuasion_world.prisoner
    prisoner.loyalty = 30
    prisoner.save(update_fields=["loyalty"])

    response = client.post(
        reverse("gameplay:recruit_prisoner_api", kwargs={"prisoner_id": prisoner.id}),
        data=json.dumps({"mode": "standard"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    result = captured_results[0]
    assert payload["success"] is True
    assert payload["recruited"] is False
    assert payload["guest_id"] is None
    assert payload["mode"] == "standard"
    assert payload["initial_loyalty"] is None
    assert payload["gold_cost"] == 1
    assert payload["copy_key"] == result.copy_key
    assert payload["copy_params"] == result.copy_params
    assert payload["text"] == result.copy_text
    _assert_no_forbidden_public_keys(payload)


def test_recruit_form_uses_story_and_unified_success_summary(client, persuasion_world, monkeypatch):
    _login(client, persuasion_world)
    captured_results = _use_recruitment_roll(monkeypatch, 1)
    persuasion_world.captor.guests.all().delete()
    prisoner = persuasion_world.prisoner
    prisoner.loyalty = 30
    prisoner.save(update_fields=["loyalty"])

    response = client.post(
        reverse("gameplay:recruit_prisoner_view", kwargs={"prisoner_id": prisoner.id}),
        data={"mode": "standard"},
    )

    rendered_messages = [message.message for message in get_messages(response.wsgi_request)]
    result = captured_results[0]
    assert rendered_messages == [f"{result.copy_text} {result.guest.display_name} 已成为 1 级门客｜初始忠诚 35。"]


def test_recruit_form_failure_redirects_with_only_failure_story(client, persuasion_world, monkeypatch):
    _login(client, persuasion_world)
    captured_results = _use_recruitment_roll(monkeypatch, 100)
    persuasion_world.captor.guests.all().delete()
    prisoner = persuasion_world.prisoner
    prisoner.loyalty = 30
    prisoner.save(update_fields=["loyalty"])

    response = client.post(
        reverse("gameplay:recruit_prisoner_view", kwargs={"prisoner_id": prisoner.id}),
        data={"mode": "standard"},
    )

    assert response.status_code == 302
    rendered_messages = list(get_messages(response.wsgi_request))
    result = captured_results[0]
    assert [message.message for message in rendered_messages] == [result.copy_text]
    assert rendered_messages[0].level_tag == "warning"
    assert "已成为 1 级门客" not in rendered_messages[0].message
    assert "概率" not in rendered_messages[0].message


def test_jail_status_api_returns_full_persuasion_payload(client, persuasion_world):
    _login(client, persuasion_world)
    response = client.get(reverse("gameplay:jail_status_api"))

    assert response.status_code == 200
    payload = response.json()
    prisoner = payload["jail"]["prisoners"][0]
    assert {"heart", "affinity", "remaining_actions", "speaker_options", "recruitment_offers"} <= set(prisoner)
    _assert_no_forbidden_public_keys(payload)


def test_jail_page_renders_complete_persuasion_workspace(client, persuasion_world):
    _login(client, persuasion_world)
    response = client.get(reverse("gameplay:jail"))

    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert 'data-jail-root="1"' in html
    assert "心防" in html
    assert "归心" in html
    assert "礼贤下士" in html
    assert "许以重利" in html
    assert "陈明大势" in html
    assert "以武慑服" in html
    assert f">{persuasion_world.strong_civil.display_name}</option>" in html
    assert f">{persuasion_world.strong_military.display_name}</option>" in html
    assert f"智力 {persuasion_world.strong_civil.template.base_intellect}" not in html
    assert f"武力 {persuasion_world.strong_military.template.base_attack}" not in html
    assert "普通收编" in html
    assert "权宜归附" in html
    assert "心悦诚服" in html
    assert f'aria-label="查看 {persuasion_world.prisoner.display_name} 的招降记录"' in html
    assert f'aria-label="释放 {persuasion_world.prisoner.display_name}"' in html
    assert reverse("gameplay:observe_prisoner_api", kwargs={"prisoner_id": persuasion_world.prisoner.id}) in html
    assert f'action="{reverse("gameplay:draw_pie_view", kwargs={"prisoner_id": persuasion_world.prisoner.id})}"' in html
    assert "画饼" not in html


def test_jail_template_reads_costs_and_recruitment_rules_from_selector_state():
    from pathlib import Path

    template = Path("gameplay/templates/gameplay/jail.html").read_text(encoding="utf-8")

    assert "p.methods.kindness.cost_text" in template
    assert "p.methods.bribe.cost_text" in template
    assert "p.methods.reason.cost_text" in template
    assert "p.methods.might.cost_text" in template
    assert "80,000 银两" not in template
    assert "p.rarity_label" not in template
    assert "p.archetype" not in template
    assert "p.original_level" not in template
    assert "p.morality" not in template
    assert "p.recruitment_offers.standard.heart_max" in template
    assert "p.recruitment_offers.negotiated.affinity_min" in template
    assert "p.recruitment_offers.heartfelt.affinity_min" in template
    assert "item.speaker_loyalty_delta" in template
    assert "item.speaker_name" in template


def test_jail_template_disables_all_recruitment_modes_after_today_attempt():
    from pathlib import Path

    template = Path("gameplay/templates/gameplay/jail.html").read_text(encoding="utf-8")

    assert template.count("or p.recruitment_attempted_today") == 3
    assert "{% if p.recruitment_attempted_today %} · 今日已尝试{% endif %}" in template
    assert "概率" not in template
    assert "胜算" not in template
