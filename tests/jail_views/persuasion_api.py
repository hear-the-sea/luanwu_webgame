from __future__ import annotations

import json

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from gameplay.services.jail_persuasion.interactions import observe_prisoner

pytestmark = pytest.mark.django_db


def _login(client, world):
    client.force_login(world.captor.user)


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


def test_interact_api_rejects_invalid_method_as_business_error(client, persuasion_world):
    _login(client, persuasion_world)
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    response = client.post(
        reverse("gameplay:interact_prisoner_api", kwargs={"prisoner_id": persuasion_world.prisoner.id}),
        data=json.dumps({"method": "unknown"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["success"] is False
    assert "未知的招降手段" in response.json()["error"]


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


def test_recruit_api_accepts_negotiated_mode(client, persuasion_world):
    _login(client, persuasion_world)
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
    assert payload["mode"] == "negotiated"
    assert payload["initial_loyalty"] == 65
    assert payload["copy_params"] == {
        "prisoner_name": prisoner.display_name,
        "new_loyalty": 65,
    }
    assert payload["text"]


def test_recruit_form_uses_unified_level_and_loyalty_summary(client, persuasion_world):
    _login(client, persuasion_world)
    persuasion_world.captor.guests.all().delete()
    prisoner = persuasion_world.prisoner
    prisoner.loyalty = 30
    prisoner.save(update_fields=["loyalty"])

    response = client.post(
        reverse("gameplay:recruit_prisoner_view", kwargs={"prisoner_id": prisoner.id}),
        data={"mode": "standard"},
    )

    rendered_messages = [message.message for message in get_messages(response.wsgi_request)]
    assert any("已成为 1 级门客｜初始忠诚 60" in message for message in rendered_messages)


def test_jail_status_api_returns_full_persuasion_payload(client, persuasion_world):
    _login(client, persuasion_world)
    response = client.get(reverse("gameplay:jail_status_api"))

    assert response.status_code == 200
    prisoner = response.json()["jail"]["prisoners"][0]
    assert {"heart", "affinity", "remaining_actions", "speaker_options", "recruitment_offers"} <= set(prisoner)


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
    assert "基础智力" in html
    assert "基础武力" in html
    assert "普通收编" in html
    assert "权宜归附" in html
    assert "心悦诚服" in html
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
    assert "p.rarity_label" in template
    assert "p.recruitment_offers.standard.heart_max" in template
    assert "p.recruitment_offers.negotiated.affinity_min" in template
    assert "p.recruitment_offers.heartfelt.affinity_min" in template
    assert "item.speaker_loyalty_delta" in template
    assert "item.speaker_name" in template
