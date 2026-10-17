"""招募保底的边界、批次顺序与事务一致性。"""

import random
from dataclasses import replace
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.config import RECRUITMENT
from gameplay.services.manor.core import ensure_manor
from guests.models import (
    GuestRarity,
    GuestRecruitment,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentPity,
    RecruitmentPool,
)
from guests.services import recruitment, recruitment_candidates, recruitment_templates
from guests.services.recruitment_pity import apply_candidate_pity


def _template(rarity, pk):
    return SimpleNamespace(rarity=rarity, id=pk)


def _draw(template, pity, *, purple=(), orange=(), excluded=()):
    return apply_candidate_pity(
        template,
        pity=pity,
        templates_by_rarity={GuestRarity.PURPLE: list(purple), GuestRarity.ORANGE: list(orange)},
        excluded_ids=set(excluded),
        rng=random.Random(7),
    )


@pytest.mark.parametrize("rarity,limit", [(GuestRarity.PURPLE, 2000), (GuestRarity.ORANGE, 8000)])
def test_pity_triggers_on_threshold_candidate_only(rarity, limit):
    assert RECRUITMENT.PURPLE_PITY_LIMIT == 2000
    assert RECRUITMENT.ORANGE_PITY_LIMIT == 8000
    common = _template(GuestRarity.GRAY, 1)
    reward = _template(rarity, 2)
    pity = SimpleNamespace(purple_misses=0, orange_misses=0)
    field = "purple_misses" if rarity == GuestRarity.PURPLE else "orange_misses"
    setattr(pity, field, limit - 2)
    pools = {"purple": [reward]} if rarity == GuestRarity.PURPLE else {"orange": [reward]}

    assert _draw(common, pity, **pools) == (common, "")
    assert getattr(pity, field) == limit - 1
    assert _draw(common, pity, **pools) == (reward, rarity)
    assert getattr(pity, field) == 0


@pytest.mark.parametrize("rarity,expected", [(GuestRarity.PURPLE, (0, 456)), (GuestRarity.ORANGE, (0, 0))])
def test_natural_high_rarity_resets_matching_counters(rarity, expected):
    pity = SimpleNamespace(purple_misses=123, orange_misses=455)
    template = _template(rarity, 1)
    assert _draw(template, pity) == (template, "")
    assert (pity.purple_misses, pity.orange_misses) == expected


def test_simultaneous_pity_prioritizes_orange_and_resets_both():
    pity = SimpleNamespace(purple_misses=1999, orange_misses=7999)
    orange = _template(GuestRarity.ORANGE, 3)
    chosen, marker = _draw(
        _template(GuestRarity.GRAY, 1),
        pity,
        purple=[_template(GuestRarity.PURPLE, 2)],
        orange=[orange],
    )
    assert chosen is orange
    assert marker == GuestRarity.ORANGE
    assert (pity.purple_misses, pity.orange_misses) == (0, 0)


def test_exhausted_pools_keep_pity_until_eligible_template_becomes_available():
    pity = SimpleNamespace(purple_misses=1999, orange_misses=7999)
    common = _template(GuestRarity.GRAY, 1)
    purple = _template(GuestRarity.PURPLE, 2)
    orange = _template(GuestRarity.ORANGE, 3)
    for _ in range(3):
        assert _draw(common, pity, purple=[purple], orange=[orange], excluded=[2, 3]) == (common, "")
        assert (pity.purple_misses, pity.orange_misses) == (2000, 8000)
    assert _draw(common, pity, purple=[purple], orange=[orange], excluded=[2]) == (
        orange,
        GuestRarity.ORANGE,
    )
    assert (pity.purple_misses, pity.orange_misses) == (0, 0)


def test_exhausted_orange_pool_still_honors_purple_and_keeps_orange_qualification():
    pity = SimpleNamespace(purple_misses=1999, orange_misses=7999)
    purple = _template(GuestRarity.PURPLE, 2)
    assert _draw(_template(GuestRarity.GRAY, 1), pity, purple=[purple]) == (purple, GuestRarity.PURPLE)
    assert (pity.purple_misses, pity.orange_misses) == (0, 8000)


def test_purple_pity_can_use_orange_when_purple_pool_exhausted():
    pity = SimpleNamespace(purple_misses=1999, orange_misses=10)
    orange = _template(GuestRarity.ORANGE, 3)
    assert _draw(_template(GuestRarity.GRAY, 1), pity, orange=[orange]) == (orange, GuestRarity.PURPLE)
    assert (pity.purple_misses, pity.orange_misses) == (0, 0)


@pytest.fixture
def pity_world(db, django_user_model, monkeypatch):
    # 只创建本组测试需要的三个模板，不导入完整门客资料库。
    manor = ensure_manor(django_user_model.objects.create_user(username="pity_player"))
    other_manor = ensure_manor(django_user_model.objects.create_user(username="pity_other_player"))
    pools = [
        RecruitmentPool.objects.create(key=f"pity_pool_{index}", name=f"保底池{index}", cost={}, draw_count=3)
        for index in range(2)
    ]
    templates = {
        rarity: [
            GuestTemplate.objects.create(
                key=f"pity_{rarity}",
                name=f"保底{rarity}",
                archetype="civil",
                rarity=rarity,
            )
        ]
        for rarity in (GuestRarity.GRAY, GuestRarity.PURPLE, GuestRarity.ORANGE)
    }
    monkeypatch.setattr(recruitment_templates, "_get_recruitable_templates_by_rarity", lambda: templates)
    monkeypatch.setattr(recruitment_templates, "_get_hermit_templates", lambda: [])
    monkeypatch.setattr(
        recruitment_templates,
        "choose_template_from_entries",
        lambda *_args, **_kwargs: templates[GuestRarity.GRAY][0],
    )
    return SimpleNamespace(manor=manor, other_manor=other_manor, pools=pools, templates=templates)


def test_batch_continues_counting_after_threshold_candidate(pity_world):
    world = pity_world
    RecruitmentPity.objects.create(manor=world.manor, purple_misses=1999, orange_misses=4500)
    candidates = recruitment._build_recruitment_candidates(
        world.manor,
        world.pools[0],
        seed=7,
        total_draw_count=3,
    )
    assert [entry.rarity for entry in candidates] == ["purple", "gray", "gray"]
    assert [entry.pity_rarity for entry in candidates] == ["purple", "", ""]
    pity = RecruitmentPity.objects.get(manor=world.manor)
    assert (pity.purple_misses, pity.orange_misses) == (2, 4503)
    assert RecruitmentCandidate.objects.get(pk=candidates[0].pk).pity_rarity == "purple"


def test_same_batch_never_reuses_its_non_repeatable_pity_reward(pity_world, monkeypatch):
    world = pity_world
    monkeypatch.setattr(
        "guests.services.recruitment_pity.RECRUITMENT",
        replace(RECRUITMENT, PURPLE_PITY_LIMIT=1),
    )
    candidates = recruitment._build_recruitment_candidates(
        world.manor,
        world.pools[0],
        seed=7,
        total_draw_count=3,
    )
    assert [entry.rarity for entry in candidates] == ["purple", "orange", "gray"]
    assert [entry.pity_rarity for entry in candidates] == ["purple", "purple", ""]
    pity = RecruitmentPity.objects.get(manor=world.manor)
    assert (pity.purple_misses, pity.orange_misses) == (1, 1)


def test_progress_persists_across_pools_and_is_isolated_per_account(pity_world):
    world = pity_world
    RecruitmentPity.objects.create(manor=world.manor, purple_misses=1998, orange_misses=7998)
    first = recruitment._build_recruitment_candidates(
        world.manor,
        world.pools[0],
        seed=7,
        total_draw_count=1,
    )
    assert first[0].rarity == "gray"
    other = recruitment._build_recruitment_candidates(
        world.other_manor,
        world.pools[1],
        seed=7,
        total_draw_count=2,
    )
    assert [entry.rarity for entry in other] == ["gray", "gray"]
    second = recruitment._build_recruitment_candidates(
        world.manor,
        world.pools[1],
        seed=7,
        total_draw_count=1,
    )
    assert (second[0].rarity, second[0].pity_rarity) == ("orange", "orange")
    own_pity = RecruitmentPity.objects.get(manor=world.manor)
    other_pity = RecruitmentPity.objects.get(manor=world.other_manor)
    assert (own_pity.purple_misses, own_pity.orange_misses) == (0, 0)
    assert (other_pity.purple_misses, other_pity.orange_misses) == (2, 2)


@pytest.mark.parametrize("already_has_progress", [False, True])
def test_failed_batch_rolls_back_progress_and_keeps_existing_candidates(pity_world, monkeypatch, already_has_progress):
    world = pity_world
    if already_has_progress:
        RecruitmentPity.objects.create(manor=world.manor, purple_misses=1999, orange_misses=7999)
    gray = world.templates[GuestRarity.GRAY][0]
    original = RecruitmentCandidate.objects.create(
        manor=world.manor,
        pool=world.pools[0],
        template=gray,
        display_name="原有候选",
        rarity=gray.rarity,
        archetype=gray.archetype,
    )
    persist = recruitment_candidates.persist_candidate_batch

    def persist_then_fail(**kwargs):
        persist(**kwargs)
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(recruitment_candidates, "persist_candidate_batch", persist_then_fail)
    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        recruitment._build_recruitment_candidates(world.manor, world.pools[0], seed=7, total_draw_count=2)
    assert list(RecruitmentCandidate.objects.filter(manor=world.manor).values_list("pk", flat=True)) == [original.pk]
    if already_has_progress:
        pity = RecruitmentPity.objects.get(manor=world.manor)
        assert (pity.purple_misses, pity.orange_misses) == (1999, 7999)
    else:
        assert not RecruitmentPity.objects.filter(manor=world.manor).exists()


def test_repeated_async_finalization_counts_candidates_once(pity_world):
    world = pity_world
    now = timezone.now()
    task = GuestRecruitment.objects.create(
        manor=world.manor,
        pool=world.pools[0],
        cost={},
        draw_count=3,
        duration_seconds=30,
        seed=7,
        complete_at=now,
    )
    assert recruitment.finalize_guest_recruitment(task, now=now) is True
    assert recruitment.finalize_guest_recruitment(task, now=now) is False
    assert recruitment.finalize_guest_recruitment(GuestRecruitment.objects.get(pk=task.pk), now=now) is False
    pity = RecruitmentPity.objects.get(manor=world.manor)
    assert (pity.purple_misses, pity.orange_misses) == (3, 3)
    assert RecruitmentCandidate.objects.filter(manor=world.manor).count() == 3
    task.refresh_from_db()
    assert task.status == GuestRecruitment.Status.COMPLETED
    assert task.result_count == 3
