from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import JailInteractionLog, JailPrisoner
from gameplay.selectors.jail import build_prisoner_state, get_jail_page_context
from gameplay.services.jail_persuasion.interactions import interact_prisoner, observe_prisoner
from gameplay.views.jail_payloads import build_jail_status_payload

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
}


def _assert_no_forbidden_public_keys(value, *, path="state"):
    if isinstance(value, dict):
        forbidden = FORBIDDEN_PUBLIC_KEYS.intersection(value)
        assert not forbidden, f"{path} exposes forbidden keys: {sorted(forbidden)}"
        for key, item in value.items():
            _assert_no_forbidden_public_keys(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_forbidden_public_keys(item, path=f"{path}[{index}]")


def _create_interaction_log(prisoner, *, usage_date, attempt_scope):
    return JailInteractionLog.objects.create(
        prisoner=prisoner,
        captor=prisoner.captor,
        method="recruitment" if attempt_scope else "kindness",
        usage_date=usage_date,
        attempt_scope=attempt_scope,
        heart_before=prisoner.loyalty,
        heart_after=prisoner.loyalty,
        affinity_before=prisoner.affinity,
        affinity_after=prisoner.affinity,
        outcome=JailInteractionLog.Outcome.NEUTRAL,
        copy_key="feedback.reason.neutral.1",
    )


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
    assert len(state["history"]) == 1
    assert state["history"][0]["text"]
    assert set(state["recruitment_offers"]) == {"standard", "negotiated", "heartfelt"}
    assert state["recruitment_offers"]["standard"]["heart_max"] == 30
    assert state["recruitment_offers"]["standard"]["affinity_min"] == 0
    assert state["recruitment_offers"]["negotiated"]["heart_max"] == 45
    assert state["recruitment_offers"]["negotiated"]["affinity_min"] == 60
    assert state["recruitment_offers"]["heartfelt"]["affinity_min"] == 100
    _assert_no_forbidden_public_keys(state)


def test_selector_speaker_options_only_expose_actionable_facts(persuasion_world):
    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner)
    reason_options = {item["id"]: item for item in state["speaker_options"]["reason"]}
    might_options = {item["id"]: item for item in state["speaker_options"]["might"]}

    weak_reason = reason_options[persuasion_world.weak_civil.id]
    strong_reason = reason_options[persuasion_world.strong_civil.id]
    assert set(weak_reason) == {
        "id",
        "name",
        "archetype",
        "speaker_base_value",
        "used_today",
        "method_used_today",
        "available",
    }
    assert weak_reason["speaker_base_value"] == 50
    assert strong_reason["speaker_base_value"] == 130
    assert might_options[persuasion_world.strong_military.id]["speaker_base_value"] == 130
    _assert_no_forbidden_public_keys(state)


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


def test_selector_exposes_pending_milestone_without_writing(persuasion_world):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    prisoner = persuasion_world.prisoner
    prisoner.refresh_from_db()
    prisoner.affinity = 35
    prisoner.save(update_fields=["affinity"])

    state = build_prisoner_state(persuasion_world.captor, prisoner)

    assert state["pending_milestone"]["stage"] == 1
    assert len(state["pending_milestone"]["choices"]) == 2
    assert all(set(choice) == {"key", "label"} for choice in state["pending_milestone"]["choices"])
    _assert_no_forbidden_public_keys(state)
    prisoner.refresh_from_db()
    assert prisoner.milestone_stage == 0


def test_selector_reports_no_recruitment_attempt_without_log(persuasion_world):
    today = timezone.localdate()

    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner, today=today)

    assert state["recruitment_attempted_today"] is False


def test_selector_reports_scoped_recruitment_attempt_for_today(persuasion_world):
    today = timezone.localdate()
    _create_interaction_log(persuasion_world.prisoner, usage_date=today, attempt_scope="recruitment")

    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner, today=today)

    assert state["recruitment_attempted_today"] is True


def test_selector_ignores_unscoped_interaction_for_recruitment_attempt(persuasion_world):
    today = timezone.localdate()
    _create_interaction_log(persuasion_world.prisoner, usage_date=today, attempt_scope=None)

    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner, today=today)

    assert state["recruitment_attempted_today"] is False


def test_selector_ignores_recruitment_attempt_from_other_date(persuasion_world):
    today = timezone.localdate()
    _create_interaction_log(
        persuasion_world.prisoner,
        usage_date=today - timedelta(days=1),
        attempt_scope="recruitment",
    )

    state = build_prisoner_state(persuasion_world.captor, persuasion_world.prisoner, today=today)

    assert state["recruitment_attempted_today"] is False


def test_page_context_batches_recruitment_attempt_lookup(persuasion_world):
    today = timezone.localdate()
    second_prisoner = JailPrisoner.objects.create(
        captor=persuasion_world.captor,
        original_manor=persuasion_world.original,
        guest_template=persuasion_world.prisoner_template,
        original_guest_name="另一名囚徒",
        original_level=10,
        loyalty=60,
        captured_loyalty=60,
    )
    _create_interaction_log(persuasion_world.prisoner, usage_date=today, attempt_scope="recruitment")

    with CaptureQueriesContext(connection) as captured:
        context = get_jail_page_context(persuasion_world.captor)

    attempt_queries = [
        query
        for query in captured.captured_queries
        if "jailinteractionlog" in query["sql"].lower() and '"attempt_scope" =' in query["sql"].lower()
    ]
    attempted_by_prisoner_id = {
        state["id"]: state["recruitment_attempted_today"] for state in context["prisoner_states"]
    }
    assert len(attempt_queries) == 1
    assert attempted_by_prisoner_id == {
        persuasion_world.prisoner.id: True,
        second_prisoner.id: False,
    }


def test_page_context_and_status_payload_include_full_persuasion_state(persuasion_world):
    context = get_jail_page_context(persuasion_world.captor)
    assert context["prisoners"][0].id == persuasion_world.prisoner.id
    assert context["prisoner_states"][0]["id"] == persuasion_world.prisoner.id

    payload = build_jail_status_payload(persuasion_world.captor, context["prisoner_states"])
    state = payload["prisoners"][0]
    assert {
        "heart",
        "affinity",
        "revealed_level",
        "remaining_actions",
        "recruitment_offers",
        "expires_at",
        "remaining_days",
    } <= set(state)
    _assert_no_forbidden_public_keys(payload, path="jail")
