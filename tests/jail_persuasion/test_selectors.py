from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.selectors.jail import build_prisoner_state, get_jail_page_context
from gameplay.services.jail_persuasion.interactions import interact_prisoner, observe_prisoner
from gameplay.views.jail_payloads import build_jail_status_payload

pytestmark = pytest.mark.django_db


def test_selector_does_not_initialize_unobserved_prisoner(persuasion_world):
    prisoner = persuasion_world.prisoner
    state = build_prisoner_state(persuasion_world.captor, prisoner)

    prisoner.refresh_from_db()
    assert prisoner.observed_at is None
    assert prisoner.stance_method == ""
    assert state["observed"] is False
    assert state["clues"] == []


def test_selector_does_not_reset_stale_daily_counter(persuasion_world, monkeypatch):
    prisoner = persuasion_world.prisoner
    stale_date = timezone.localdate() - timedelta(days=1)
    prisoner.interaction_date = stale_date
    prisoner.interactions_today = 3
    prisoner.save(update_fields=["interaction_date", "interactions_today"])
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)

    state = build_prisoner_state(persuasion_world.captor, prisoner)

    prisoner.refresh_from_db()
    assert prisoner.interaction_date == stale_date
    assert prisoner.interactions_today == 3
    assert state["daily_limit"] == 2
    assert state["interactions_today"] == 0
    assert state["remaining_actions"] == 2


def test_selector_builds_clues_history_methods_and_recruitment_offers(persuasion_world, monkeypatch):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    interact_prisoner(persuasion_world.captor, persuasion_world.prisoner.id, method="kindness")
    prisoner = persuasion_world.prisoner
    prisoner.refresh_from_db()

    state = build_prisoner_state(persuasion_world.captor, prisoner)

    assert state["heart"] == prisoner.loyalty
    assert state["affinity"] == prisoner.affinity
    assert state["captured_loyalty"] == 80
    assert state["original_level"] == 20
    assert state["rarity"] == "green"
    assert state["rarity_label"] == "绿"
    assert len(state["clues"]) == 2
    assert state["methods"]["kindness"]["cost"] == {"silver": 80_000, "grain": 5_000}
    assert state["methods"]["kindness"]["cost_text"] == "80,000 银两 · 5,000 粮食"
    assert set(state["methods"]["kindness"]["effect_range"]) == {
        "heart_min",
        "heart_max",
        "affinity_min",
        "affinity_max",
        "outcome",
    }
    assert len(state["history"]) == 1
    assert state["history"][0]["text"]
    assert set(state["recruitment_offers"]) == {"standard", "negotiated", "heartfelt"}
    assert state["recruitment_offers"]["standard"]["heart_max"] == 30
    assert state["recruitment_offers"]["standard"]["affinity_min"] == 0
    assert state["recruitment_offers"]["negotiated"]["heart_max"] == 45
    assert state["recruitment_offers"]["negotiated"]["affinity_min"] == 60
    assert state["recruitment_offers"]["heartfelt"]["affinity_min"] == 100


def test_selector_builds_speaker_base_value_ratios_and_risk_labels(persuasion_world):
    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner)
    reason_options = {item["id"]: item for item in state["speaker_options"]["reason"]}
    might_options = {item["id"]: item for item in state["speaker_options"]["might"]}

    weak_reason = reason_options[persuasion_world.weak_civil.id]
    failed_reason = reason_options[persuasion_world.failed_civil.id]
    strong_reason = reason_options[persuasion_world.strong_civil.id]
    assert (weak_reason["speaker_base_value"], weak_reason["prisoner_base_value"], weak_reason["risk"]) == (
        50,
        100,
        "backfire",
    )
    assert failed_reason["risk"] == "failed"
    assert strong_reason["risk"] == "advantage"
    assert might_options[persuasion_world.strong_military.id]["risk"] == "advantage"
    assert strong_reason["effect_range"]["heart_max"] < 0
    assert strong_reason["effect_range"]["affinity_min"] > 0


def test_selector_history_keeps_speaker_loyalty_delta(persuasion_world, monkeypatch):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    persuasion_world.prisoner.affinity = 10
    persuasion_world.prisoner.save(update_fields=["affinity"])
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method="reason",
        speaker_id=persuasion_world.weak_civil.id,
    )

    history = build_prisoner_state(
        persuasion_world.captor,
        persuasion_world.prisoner,
    )["history"]
    assert history[0]["speaker_name"] == persuasion_world.weak_civil.display_name
    assert history[0]["speaker_loyalty_delta"] == -1


def test_selector_clamps_ratio_without_changing_base_value_snapshots(persuasion_world):
    persuasion_world.strong_civil.template.base_intellect = 300
    persuasion_world.strong_civil.template.save(update_fields=["base_intellect"])

    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner)
    reason_options = {item["id"]: item for item in state["speaker_options"]["reason"]}
    option = reason_options[persuasion_world.strong_civil.id]

    assert option["ratio"] == 1.5
    assert option["speaker_base_value"] == 300
    assert option["prisoner_base_value"] == 100


def test_level_one_preview_uses_a_layer_before_hidden_state_is_confirmed(persuasion_world):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    prisoner = persuasion_world.prisoner
    prisoner.refresh_from_db()
    prisoner.taboo_method = "kindness"

    level_one = build_prisoner_state(persuasion_world.captor, prisoner)
    assert level_one["methods"]["kindness"]["effect_range"]["affinity_min"] >= 0

    prisoner.revealed_level = 2
    level_two = build_prisoner_state(persuasion_world.captor, prisoner)
    assert level_two["methods"]["kindness"]["effect_range"]["outcome"] == "taboo"
    assert level_two["methods"]["kindness"]["effect_range"]["heart_min"] == 3


def test_hidden_stance_preview_is_wide_then_narrows_after_second_clue(persuasion_world):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    prisoner = persuasion_world.prisoner
    prisoner.refresh_from_db()
    prisoner.stance_method = "kindness"
    prisoner.revealed_level = 1

    wide = build_prisoner_state(persuasion_world.captor, prisoner)["methods"]["kindness"]["effect_range"]

    prisoner.revealed_level = 2
    narrow = build_prisoner_state(persuasion_world.captor, prisoner)["methods"]["kindness"]["effect_range"]

    assert wide["heart_min"] <= narrow["heart_min"] <= narrow["heart_max"] <= wide["heart_max"]
    assert wide["affinity_min"] <= narrow["affinity_min"] <= narrow["affinity_max"] <= wide["affinity_max"]
    assert wide["heart_max"] - wide["heart_min"] > narrow["heart_max"] - narrow["heart_min"]
    assert wide["affinity_max"] - wide["affinity_min"] > narrow["affinity_max"] - narrow["affinity_min"]


def test_selector_exposes_pending_milestone_without_writing(persuasion_world):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    prisoner = persuasion_world.prisoner
    prisoner.refresh_from_db()
    prisoner.affinity = 35
    prisoner.save(update_fields=["affinity"])

    state = build_prisoner_state(persuasion_world.captor, prisoner)

    assert state["pending_milestone"]["stage"] == 1
    assert len(state["pending_milestone"]["choices"]) == 2
    prisoner.refresh_from_db()
    assert prisoner.milestone_stage == 0


def test_page_context_and_status_payload_include_full_persuasion_state(persuasion_world):
    context = get_jail_page_context(persuasion_world.captor)
    assert context["prisoners"][0].id == persuasion_world.prisoner.id
    assert context["prisoner_states"][0]["id"] == persuasion_world.prisoner.id

    payload = build_jail_status_payload(persuasion_world.captor, context["prisoner_states"])
    state = payload["prisoners"][0]
    assert {"heart", "affinity", "revealed_level", "remaining_actions", "recruitment_offers"} <= set(state)
