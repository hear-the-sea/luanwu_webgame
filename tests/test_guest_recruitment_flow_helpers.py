from __future__ import annotations

import random
from types import SimpleNamespace

import guests.services.recruitment_candidates as recruitment_candidates
import guests.services.recruitment_flow as recruitment_flow
import guests.services.recruitment_guests as recruitment_guests
import guests.services.recruitment_queries as recruitment_queries
from core.exceptions import RecruitmentAlreadyInProgressError, RecruitmentDailyLimitExceededError
from guests.models import GuestRarity


def test_resolve_recruitment_seed_keeps_explicit_value():
    assert recruitment_flow.resolve_recruitment_seed(12345) == 12345


def test_resolve_recruitment_seed_rejects_invalid_value():
    with __import__("pytest").raises(AssertionError, match="invalid recruitment seed"):
        recruitment_flow.resolve_recruitment_seed("bad-seed")


def test_resolve_recruitment_seed_rejects_non_positive_value():
    with __import__("pytest").raises(AssertionError, match="invalid recruitment seed"):
        recruitment_flow.resolve_recruitment_seed(0)


def test_resolve_recruitment_cost_returns_copy():
    pool = SimpleNamespace(cost={"silver": 100})

    resolved = recruitment_flow.resolve_recruitment_cost(pool)
    resolved["silver"] = 200

    assert pool.cost["silver"] == 100


def test_resolve_recruitment_cost_accepts_sequence_of_pairs():
    pool = SimpleNamespace(cost=[("silver", 100)])

    resolved = recruitment_flow.resolve_recruitment_cost(pool)

    assert resolved == {"silver": 100}


def test_resolve_recruitment_cost_rejects_invalid_payload():
    pool = SimpleNamespace(cost="bad-cost")

    with __import__("pytest").raises(AssertionError, match="invalid recruitment cost payload"):
        recruitment_flow.resolve_recruitment_cost(pool)


def test_resolve_recruitment_cost_rejects_falsey_scalar_payload():
    pool = SimpleNamespace(cost=False)

    with __import__("pytest").raises(AssertionError, match="invalid recruitment cost payload"):
        recruitment_flow.resolve_recruitment_cost(pool)


def test_create_pending_recruitment_rejects_invalid_draw_count():
    recruitment_model = SimpleNamespace(objects=SimpleNamespace(create=lambda **_kwargs: _kwargs))

    with __import__("pytest").raises(AssertionError, match="invalid recruitment draw count"):
        recruitment_flow.create_pending_recruitment(
            recruitment_model=recruitment_model,
            manor="manor",
            pool="pool",
            current_time=__import__("datetime").datetime(2026, 1, 1),
            cost={},
            draw_count="bad-draw-count",
            duration_seconds=30,
            seed=7,
        )


def test_create_pending_recruitment_rejects_non_positive_draw_count():
    recruitment_model = SimpleNamespace(objects=SimpleNamespace(create=lambda **_kwargs: _kwargs))

    with __import__("pytest").raises(AssertionError, match="invalid recruitment draw count"):
        recruitment_flow.create_pending_recruitment(
            recruitment_model=recruitment_model,
            manor="manor",
            pool="pool",
            current_time=__import__("datetime").datetime(2026, 1, 1),
            cost={},
            draw_count=0,
            duration_seconds=30,
            seed=7,
        )


def test_create_pending_recruitment_rejects_invalid_duration():
    recruitment_model = SimpleNamespace(objects=SimpleNamespace(create=lambda **_kwargs: _kwargs))

    with __import__("pytest").raises(AssertionError, match="invalid recruitment duration"):
        recruitment_flow.create_pending_recruitment(
            recruitment_model=recruitment_model,
            manor="manor",
            pool="pool",
            current_time=__import__("datetime").datetime(2026, 1, 1),
            cost={},
            draw_count=1,
            duration_seconds="bad-duration",
            seed=7,
        )


def test_create_pending_recruitment_rejects_non_positive_seed():
    recruitment_model = SimpleNamespace(objects=SimpleNamespace(create=lambda **_kwargs: _kwargs))

    with __import__("pytest").raises(AssertionError, match="invalid recruitment seed"):
        recruitment_flow.create_pending_recruitment(
            recruitment_model=recruitment_model,
            manor="manor",
            pool="pool",
            current_time=__import__("datetime").datetime(2026, 1, 1),
            cost={},
            draw_count=1,
            duration_seconds=30,
            seed=0,
        )


def test_create_pending_recruitment_rejects_non_positive_duration():
    recruitment_model = SimpleNamespace(objects=SimpleNamespace(create=lambda **_kwargs: _kwargs))

    with __import__("pytest").raises(AssertionError, match="invalid recruitment duration"):
        recruitment_flow.create_pending_recruitment(
            recruitment_model=recruitment_model,
            manor="manor",
            pool="pool",
            current_time=__import__("datetime").datetime(2026, 1, 1),
            cost={},
            draw_count=1,
            duration_seconds=0,
            seed=7,
        )


def test_resolve_candidate_draw_count_includes_tavern_bonus():
    pool = SimpleNamespace(draw_count=2)
    manor = SimpleNamespace(tavern_recruitment_bonus=3)

    assert recruitment_candidates.resolve_candidate_draw_count(pool=pool, manor=manor, total_draw_count=None) == 5


def test_resolve_candidate_draw_count_rejects_invalid_value():
    pool = SimpleNamespace(draw_count=2)
    manor = SimpleNamespace(tavern_recruitment_bonus=3)

    with __import__("pytest").raises(AssertionError, match="invalid recruitment draw count"):
        recruitment_candidates.resolve_candidate_draw_count(pool=pool, manor=manor, total_draw_count="bad-count")


def test_resolve_candidate_draw_count_rejects_non_positive_value():
    pool = SimpleNamespace(draw_count=2)
    manor = SimpleNamespace(tavern_recruitment_bonus=3)

    with __import__("pytest").raises(AssertionError, match="invalid recruitment draw count"):
        recruitment_candidates.resolve_candidate_draw_count(pool=pool, manor=manor, total_draw_count=0)


def test_build_candidate_display_name_uses_random_name_for_common_candidates():
    template = SimpleNamespace(name="模板名", rarity=GuestRarity.GRAY, is_hermit=False)

    result = recruitment_candidates.build_candidate_display_name(
        template,
        random.Random(1),
        generate_random_name=lambda _rng: "随机名",
    )

    assert result == "随机名"


def test_build_candidate_display_name_keeps_template_name_for_hermit():
    template = SimpleNamespace(name="隐士", rarity=GuestRarity.BLACK, is_hermit=True)

    result = recruitment_candidates.build_candidate_display_name(
        template,
        random.Random(1),
        generate_random_name=lambda _rng: "随机名",
    )

    assert result == "隐士"


def test_build_recruitment_custom_name_matches_normal_common_guest_naming():
    common_template = SimpleNamespace(rarity=GuestRarity.GRAY, is_hermit=False)
    hermit_template = SimpleNamespace(rarity=GuestRarity.BLACK, is_hermit=True)

    custom_name = recruitment_guests.build_recruitment_custom_name(common_template, random.Random(1))

    assert custom_name
    assert recruitment_guests.build_recruitment_custom_name(hermit_template, random.Random(1)) == ""


def test_candidate_template_rules_match_repeat_and_reveal_design():
    red_template = SimpleNamespace(id=1, rarity=GuestRarity.RED, is_hermit=False)
    hermit_template = SimpleNamespace(id=2, rarity=GuestRarity.BLACK, is_hermit=True)

    assert recruitment_candidates.should_reveal_candidate_rarity(red_template) is True
    assert (
        recruitment_candidates.should_exclude_candidate_template(
            hermit_template,
            non_repeatable_rarities=frozenset({GuestRarity.GREEN, GuestRarity.BLUE, GuestRarity.RED}),
        )
        is True
    )


def test_validate_recruitment_start_allowed_rejects_active_recruitment():
    manor = SimpleNamespace(pk=1)
    pool = SimpleNamespace(pk=2, name="村募")

    with __import__("pytest").raises(RecruitmentAlreadyInProgressError, match="已有招募正在进行中"):
        recruitment_flow.validate_recruitment_start_allowed(
            locked_manor=manor,
            pool=pool,
            current_time="now",
            has_active_guest_recruitment=lambda _manor: True,
            daily_limit=3,
            count_pool_draws_today=lambda *_args, **_kwargs: 0,
        )


def test_validate_recruitment_start_allowed_rejects_daily_limit():
    manor = SimpleNamespace(pk=1)
    pool = SimpleNamespace(pk=2, name="村募")

    with __import__("pytest").raises(RecruitmentDailyLimitExceededError, match="今日招募次数已达上限"):
        recruitment_flow.validate_recruitment_start_allowed(
            locked_manor=manor,
            pool=pool,
            current_time="now",
            has_active_guest_recruitment=lambda _manor: False,
            daily_limit=2,
            count_pool_draws_today=lambda *_args, **_kwargs: 2,
        )


def test_spend_recruitment_cost_if_needed_skips_empty_cost_and_formats_note():
    calls = []

    recruitment_flow.spend_recruitment_cost_if_needed(
        manor="manor",
        cost={},
        pool_name="村募",
        spend_resources=lambda *args, **kwargs: calls.append((args, kwargs)),
        recruit_cost_reason="reason",
    )
    assert calls == []

    recruitment_flow.spend_recruitment_cost_if_needed(
        manor="manor",
        cost={"silver": 100},
        pool_name="村募",
        spend_resources=lambda *args, **kwargs: calls.append((args, kwargs)),
        recruit_cost_reason="reason",
    )
    assert calls[0][0] == ("manor", {"silver": 100})
    assert calls[0][1]["note"] == "卡池：村募"
    assert calls[0][1]["reason"] == "reason"


def test_load_candidate_generation_context_uses_injected_loaders_once():
    calls = {"by_rarity": 0, "hermit": 0, "excluded": 0}

    class _Entries:
        def select_related(self, *_args, **_kwargs):
            return ["entry-1"]

    context = recruitment_candidates.load_candidate_generation_context(
        manor=SimpleNamespace(tavern_recruitment_bonus=1),
        pool=SimpleNamespace(draw_count=2, entries=_Entries()),
        seed=7,
        total_draw_count=None,
        get_recruitable_templates_by_rarity=lambda: calls.__setitem__("by_rarity", calls["by_rarity"] + 1)
        or {"gray": []},
        get_hermit_templates=lambda: calls.__setitem__("hermit", calls["hermit"] + 1) or [],
        get_excluded_template_ids=lambda _manor: calls.__setitem__("excluded", calls["excluded"] + 1) or {1, 2},
    )

    assert context["pool_entries"] == ["entry-1"]
    assert context["resolved_draw_count"] == 3
    assert context["excluded_ids"] == {1, 2}
    assert calls == {"by_rarity": 1, "hermit": 1, "excluded": 1}


def test_load_candidate_generation_context_rejects_invalid_seed():
    class _Entries:
        def select_related(self, *_args, **_kwargs):
            return []

    with __import__("pytest").raises(AssertionError, match="invalid recruitment seed"):
        recruitment_candidates.load_candidate_generation_context(
            manor=SimpleNamespace(tavern_recruitment_bonus=0),
            pool=SimpleNamespace(draw_count=1, entries=_Entries()),
            seed="bad-seed",
            total_draw_count=None,
            get_recruitable_templates_by_rarity=lambda: {},
            get_hermit_templates=lambda: [],
            get_excluded_template_ids=lambda _manor: set(),
        )


def test_get_pool_recruitment_duration_seconds_rejects_non_positive_value():
    pool = SimpleNamespace(cooldown_seconds=0)

    with __import__("pytest").raises(AssertionError, match="invalid recruitment cooldown"):
        recruitment_queries.get_pool_recruitment_duration_seconds(pool)


def test_get_pool_daily_draw_limit_rejects_non_positive_value(monkeypatch):
    monkeypatch.setattr(recruitment_queries, "RECRUITMENT", SimpleNamespace(DAILY_POOL_DRAW_LIMIT=0))

    with __import__("pytest").raises(AssertionError, match="invalid recruitment daily limit"):
        recruitment_queries._get_pool_daily_draw_limit()


def test_mark_recruitment_completed_locked_rejects_negative_result_count():
    saved = {}
    invalidations = []
    recruitment = SimpleNamespace(
        manor_id=7,
        status="pending",
        finished_at=None,
        result_count=0,
        error_message="boom",
        save=lambda **kwargs: saved.update(kwargs),
    )

    with __import__("pytest").raises(AssertionError, match="invalid recruitment result count"):
        recruitment_flow.mark_recruitment_completed_locked(
            recruitment,
            current_time=__import__("datetime").datetime(2026, 1, 1),
            result_count=-1,
            invalidate_cache=lambda manor_id: invalidations.append(manor_id),
        )

    assert saved == {}
    assert invalidations == []
