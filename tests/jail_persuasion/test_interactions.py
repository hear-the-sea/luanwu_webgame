from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from core.exceptions import JailError
from gameplay.models import InventoryItem, JailInteractionLog, JailPrisoner
from gameplay.services.jail import draw_pie
from gameplay.services.jail_persuasion.interactions import (
    _create_log_with_speaker_guard,
    interact_prisoner,
    observe_prisoner,
)
from gameplay.services.jail_persuasion.profiles import METHOD_BRIBE, METHOD_KINDNESS, METHOD_MIGHT, METHOD_REASON

pytestmark = pytest.mark.django_db


def _observe(world):
    return observe_prisoner(world.captor, world.prisoner.id)


def test_observe_persists_stance_taboo_and_first_clue_once(persuasion_world):
    result = _observe(persuasion_world)

    result.prisoner.refresh_from_db()
    assert result.prisoner.observed_at is not None
    assert result.prisoner.revealed_level == 1
    assert result.prisoner.stance_method in {METHOD_KINDNESS, METHOD_REASON}
    assert result.prisoner.taboo_method == METHOD_BRIBE
    assert len(result.clue_keys) == 1

    with pytest.raises(JailError, match="已经察言观色"):
        _observe(persuasion_world)


def test_interaction_requires_observation_unless_legacy_lazy_path(persuasion_world, monkeypatch):
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    with pytest.raises(JailError, match="先察言观色"):
        interact_prisoner(persuasion_world.captor, persuasion_world.prisoner.id, method=METHOD_BRIBE)

    result = interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_BRIBE,
        lazy_observe=True,
    )
    assert result.prisoner.observed_at is not None
    assert result.prisoner.interactions_today == 1


def test_kindness_consumes_exact_resources_and_records_log(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_KINDNESS,
    )

    persuasion_world.captor.refresh_from_db()
    result.prisoner.refresh_from_db()
    assert persuasion_world.captor.silver == 120_000
    assert persuasion_world.captor.grain == 15_000
    assert result.log.resource_cost == {"silver": 80_000, "grain": 5_000}
    assert result.prisoner.revealed_level == 2
    assert result.prisoner.interactions_today == 1
    assert result.heart_delta < 0
    assert result.affinity_delta > 0


def test_level_one_jail_allows_one_interaction_per_day(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 1)

    interact_prisoner(persuasion_world.captor, persuasion_world.prisoner.id, method=METHOD_KINDNESS)
    with pytest.raises(JailError, match="今日招降次数已用完"):
        interact_prisoner(persuasion_world.captor, persuasion_world.prisoner.id, method=METHOD_BRIBE)


def test_stale_interaction_date_resets_only_on_post(persuasion_world, monkeypatch):
    prisoner = persuasion_world.prisoner
    prisoner.interaction_date = timezone.localdate() - timedelta(days=1)
    prisoner.interactions_today = 3
    prisoner.save(update_fields=["interaction_date", "interactions_today"])
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(persuasion_world.captor, prisoner.id, method=METHOD_KINDNESS)
    assert result.prisoner.interaction_date == timezone.localdate()
    assert result.prisoner.interactions_today == 1


def test_failed_reason_consumes_daily_uses_without_state_change(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    before = (persuasion_world.prisoner.loyalty, persuasion_world.prisoner.affinity)

    result = interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_REASON,
        speaker_id=persuasion_world.failed_civil.id,
    )

    result.prisoner.refresh_from_db()
    persuasion_world.failed_civil.refresh_from_db()
    assert result.outcome == "failed"
    assert (result.prisoner.loyalty, result.prisoner.affinity) == before
    assert result.prisoner.interactions_today == 1
    assert persuasion_world.failed_civil.loyalty == 70
    assert result.log.outcome == "failed"


def test_reason_backfire_updates_prisoner_and_speaker_atomically(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    persuasion_world.prisoner.affinity = 10
    persuasion_world.prisoner.save(update_fields=["affinity"])
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_REASON,
        speaker_id=persuasion_world.weak_civil.id,
    )

    result.prisoner.refresh_from_db()
    persuasion_world.weak_civil.refresh_from_db()
    assert result.outcome == "backfire"
    assert (result.heart_delta, result.affinity_delta, result.speaker_loyalty_delta) == (2, -4, -1)
    assert result.prisoner.loyalty == 82
    assert result.prisoner.affinity == 6
    assert persuasion_world.weak_civil.loyalty == 69
    assert (result.log.speaker_loyalty_before, result.log.speaker_loyalty_after) == (70, 69)


def test_backfire_keeps_zero_speaker_loyalty_at_zero(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    persuasion_world.weak_civil.loyalty = 0
    persuasion_world.weak_civil.save(update_fields=["loyalty"])
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_REASON,
        speaker_id=persuasion_world.weak_civil.id,
    )

    persuasion_world.weak_civil.refresh_from_db()
    assert result.outcome == "backfire"
    assert result.speaker_loyalty_delta == 0
    assert result.speaker_loyalty == 0
    assert persuasion_world.weak_civil.loyalty == 0
    assert (result.log.speaker_loyalty_before, result.log.speaker_loyalty_after) == (0, 0)


def test_kindness_rolls_back_resources_and_prisoner_when_log_write_fails(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    prisoner_before = (
        persuasion_world.prisoner.loyalty,
        persuasion_world.prisoner.affinity,
        persuasion_world.prisoner.interactions_today,
    )
    resources_before = (persuasion_world.captor.silver, persuasion_world.captor.grain)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    monkeypatch.setattr(
        "gameplay.services.jail_persuasion.interactions._create_log_with_speaker_guard",
        lambda **values: (_ for _ in ()).throw(JailError("日志写入失败")),
    )

    with pytest.raises(JailError, match="日志写入失败"):
        interact_prisoner(
            persuasion_world.captor,
            persuasion_world.prisoner.id,
            method=METHOD_KINDNESS,
        )

    persuasion_world.captor.refresh_from_db()
    persuasion_world.prisoner.refresh_from_db()
    assert (persuasion_world.captor.silver, persuasion_world.captor.grain) == resources_before
    assert (
        persuasion_world.prisoner.loyalty,
        persuasion_world.prisoner.affinity,
        persuasion_world.prisoner.interactions_today,
    ) == prisoner_before
    assert JailInteractionLog.objects.filter(prisoner=persuasion_world.prisoner).count() == 0


def test_backfire_rolls_back_speaker_and_prisoner_when_log_write_fails(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    persuasion_world.prisoner.affinity = 10
    persuasion_world.prisoner.save(update_fields=["affinity"])
    prisoner_before = (
        persuasion_world.prisoner.loyalty,
        persuasion_world.prisoner.affinity,
        persuasion_world.prisoner.interactions_today,
    )
    speaker_loyalty_before = persuasion_world.weak_civil.loyalty
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    monkeypatch.setattr(
        "gameplay.services.jail_persuasion.interactions._create_log_with_speaker_guard",
        lambda **values: (_ for _ in ()).throw(JailError("日志写入失败")),
    )

    with pytest.raises(JailError, match="日志写入失败"):
        interact_prisoner(
            persuasion_world.captor,
            persuasion_world.prisoner.id,
            method=METHOD_REASON,
            speaker_id=persuasion_world.weak_civil.id,
        )

    persuasion_world.prisoner.refresh_from_db()
    persuasion_world.weak_civil.refresh_from_db()
    assert (
        persuasion_world.prisoner.loyalty,
        persuasion_world.prisoner.affinity,
        persuasion_world.prisoner.interactions_today,
    ) == prisoner_before
    assert persuasion_world.weak_civil.loyalty == speaker_loyalty_before
    assert JailInteractionLog.objects.filter(prisoner=persuasion_world.prisoner).count() == 0


def test_unrelated_log_integrity_error_is_not_reported_as_speaker_reuse(persuasion_world, monkeypatch):
    database_error = IntegrityError("unrelated log constraint")
    monkeypatch.setattr(
        JailInteractionLog.objects,
        "create",
        lambda **values: (_ for _ in ()).throw(database_error),
    )

    with pytest.raises(IntegrityError, match="unrelated log constraint"):
        _create_log_with_speaker_guard(
            speaker=persuasion_world.strong_civil,
            usage_date=timezone.localdate(),
        )


def test_taboo_precedes_speaker_backfire_and_does_not_reduce_speaker_loyalty(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    prisoner = persuasion_world.prisoner
    prisoner.taboo_method = METHOD_MIGHT
    prisoner.affinity = 10
    prisoner.save(update_fields=["taboo_method", "affinity"])
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 3)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(
        persuasion_world.captor,
        prisoner.id,
        method=METHOD_MIGHT,
        speaker_id=persuasion_world.weak_military.id,
    )

    persuasion_world.weak_military.refresh_from_db()
    assert result.outcome == "taboo"
    assert (result.heart_delta, result.affinity_delta, result.speaker_loyalty_delta) == (3, -8, 0)
    assert persuasion_world.weak_military.loyalty == 70


def test_speaker_cannot_be_used_for_two_prisoners_on_same_date(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 5)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))
    second = JailPrisoner.objects.create(
        captor=persuasion_world.captor,
        original_manor=persuasion_world.original,
        guest_template=persuasion_world.prisoner_template,
        original_guest_name="另一名囚徒",
        original_level=10,
        loyalty=70,
        captured_loyalty=70,
    )
    observe_prisoner(persuasion_world.captor, second.id)

    interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_REASON,
        speaker_id=persuasion_world.strong_civil.id,
    )
    with pytest.raises(JailError, match="今日已经担任过说客"):
        interact_prisoner(
            persuasion_world.captor,
            second.id,
            method=METHOD_REASON,
            speaker_id=persuasion_world.strong_civil.id,
        )
    assert JailInteractionLog.objects.filter(speaker=persuasion_world.strong_civil).count() == 1


def test_each_speaker_method_can_only_be_used_once_per_prisoner_per_day(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 5)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    interact_prisoner(
        persuasion_world.captor,
        persuasion_world.prisoner.id,
        method=METHOD_REASON,
        speaker_id=persuasion_world.strong_civil.id,
    )
    with pytest.raises(JailError, match="今日已经使用过陈明大势"):
        interact_prisoner(
            persuasion_world.captor,
            persuasion_world.prisoner.id,
            method=METHOD_REASON,
            speaker_id=persuasion_world.failed_civil.id,
        )


def test_bribe_consumes_one_gold_bar_and_persists_copy_key(persuasion_world, monkeypatch):
    _observe(persuasion_world)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    result = interact_prisoner(persuasion_world.captor, persuasion_world.prisoner.id, method=METHOD_BRIBE)
    inventory = InventoryItem.objects.get(manor=persuasion_world.captor, template=persuasion_world.gold_template)
    assert inventory.quantity == 9
    assert result.log.copy_key.startswith("feedback.bribe.")
    assert result.copy_text


def test_legacy_draw_pie_lazily_observes_and_uses_bribe_rules(persuasion_world, monkeypatch):
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    prisoner = draw_pie(persuasion_world.captor, persuasion_world.prisoner.id)

    prisoner.refresh_from_db()
    assert prisoner.observed_at is not None
    assert prisoner.interactions_today == 1
    assert prisoner.last_method == METHOD_BRIBE
    assert prisoner._persuasion_result.outcome in {"matched", "neutral", "taboo"}
    assert prisoner._reduction == max(0, -prisoner._persuasion_result.heart_delta)
