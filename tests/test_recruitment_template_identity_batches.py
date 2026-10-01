from __future__ import annotations

import random
from functools import partial

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.exceptions import GuestAlreadyOwnedError
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestRarity, GuestTemplate, RecruitmentCandidate, RecruitmentPool, RecruitmentPoolEntry
from guests.services.recruitment_candidates import build_candidate_batch
from guests.services.recruitment_finalize_helpers import (
    ensure_candidate_template_available,
    partition_non_repeatable_candidates,
)
from guests.services.recruitment_guests import bulk_finalize_candidates
from guests.services.recruitment_shared import NON_REPEATABLE_RARITIES
from guests.services.recruitment_templates import choose_template_from_entries
from guests.services.template_identity import get_non_repeatable_family_template_ids_by_id


@pytest.fixture
def identity_batch_world(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="identity_batch"))
    pool = RecruitmentPool.objects.create(key="identity_batch", name="人物判重", draw_count=3)
    family = [
        GuestTemplate.objects.create(key=key, name="同一人物", rarity=rarity, archetype="military")
        for key, rarity in zip(
            ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
            (GuestRarity.GREEN, GuestRarity.BLUE, GuestRarity.PURPLE),
        )
    ]
    common = GuestTemplate.objects.create(
        key="identity_repeatable", name="普通门客", rarity=GuestRarity.GRAY, archetype="military"
    )
    return manor, pool, family, common


@pytest.mark.django_db
@pytest.mark.parametrize("reverse", [False, True])
def test_empty_manor_batch_accepts_only_first_upgrade_family_candidate(identity_batch_world, reverse):
    manor, pool, family, common = identity_batch_world
    ordered = list(reversed(family)) if reverse else family
    templates = [*ordered, common, common]
    candidates = [
        RecruitmentCandidate(manor=manor, pool=pool, template=template, rarity=template.rarity)
        for template in templates
    ]

    with CaptureQueriesContext(connection) as queries:
        accepted, rejected = partition_non_repeatable_candidates(manor, candidates)

    assert accepted == [candidates[0], candidates[3], candidates[4]]
    assert rejected == candidates[1:3]
    assert len(queries) == 3  # 庄园已有模板、整批候选、升阶版本各一次。


@pytest.mark.django_db
@pytest.mark.parametrize("explicit_entries", [False, True])
def test_candidate_generation_excludes_other_versions_after_first_family_draw(identity_batch_world, explicit_entries):
    manor, pool, family, common = identity_batch_world
    templates_by_rarity = {}
    entries = []
    for template in [*family, common]:
        templates_by_rarity.setdefault(template.rarity, []).append(template)
        entries.append(
            RecruitmentPoolEntry.objects.create(
                pool=pool,
                template=template if explicit_entries else None,
                rarity=template.rarity,
                weight=1,
            )
        )
    excluded_ids = set()
    with CaptureQueriesContext(connection) as queries:
        candidates = build_candidate_batch(
            manor=manor,
            pool=pool,
            pool_entries=entries,
            resolved_draw_count=10,
            excluded_ids=excluded_ids,
            rng=random.Random(1),
            choose_template_from_entries=partial(choose_template_from_entries, rarity=GuestRarity.GREEN),
            templates_by_rarity=templates_by_rarity,
            hermit_templates=[],
            generate_random_name=lambda _rng: "普通门客",
            non_repeatable_rarities=NON_REPEATABLE_RARITIES,
        )
    family_ids = {template.pk for template in family}
    assert sum(candidate.template_id in family_ids for candidate in candidates) == 1
    assert all(candidate.template_id in family_ids or candidate.template_id == common.pk for candidate in candidates)
    assert excluded_ids == family_ids
    assert len(queries) == 2  # 候选数量不会放大人物关系查询数。


@pytest.mark.django_db
def test_bulk_finalize_persists_only_one_guest_from_same_family(identity_batch_world):
    manor, pool, family, _common = identity_batch_world
    candidates = [
        RecruitmentCandidate.objects.create(
            manor=manor,
            pool=pool,
            template=template,
            rarity=template.rarity,
            archetype=template.archetype,
            display_name=template.name,
        )
        for template in family
    ]

    created, failed = bulk_finalize_candidates(candidates)

    assert [guest.template_id for guest in created] == [family[0].pk]
    assert [candidate.pk for candidate in failed] == [candidate.pk for candidate in candidates[1:]]
    assert list(manor.guests.values_list("template_id", flat=True)) == [family[0].pk]
    assert set(RecruitmentCandidate.objects.filter(manor=manor).values_list("pk", flat=True)) == {
        candidate.pk for candidate in candidates[1:]
    }


@pytest.mark.django_db
def test_candidate_snapshot_rarity_still_prevents_exact_template_duplicates(identity_batch_world):
    manor, pool, _family, common = identity_batch_world
    candidates = [
        RecruitmentCandidate(manor=manor, pool=pool, template=common, rarity=GuestRarity.GREEN) for _ in range(2)
    ]

    accepted, rejected = partition_non_repeatable_candidates(manor, candidates)

    assert accepted == candidates[:1]
    assert rejected == candidates[1:]


@pytest.mark.django_db
@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("candidate_rarity", [GuestRarity.GREEN, GuestRarity.GRAY])
def test_owned_exact_template_preserves_candidate_snapshot_uniqueness(identity_batch_world, single, candidate_rarity):
    manor, pool, _family, common = identity_batch_world
    Guest.objects.create(manor=manor, template=common)
    candidate = RecruitmentCandidate(manor=manor, pool=pool, template=common, rarity=candidate_rarity)
    non_repeatable = candidate_rarity == GuestRarity.GREEN

    if single:
        if non_repeatable:
            with pytest.raises(GuestAlreadyOwnedError):
                ensure_candidate_template_available(manor, candidate)
        else:
            ensure_candidate_template_available(manor, candidate)
    else:
        accepted, rejected = partition_non_repeatable_candidates(manor, [candidate])
        assert accepted == ([] if non_repeatable else [candidate])
        assert rejected == ([candidate] if non_repeatable else [])


@pytest.mark.django_db
def test_candidate_generation_preserves_caller_non_repeatable_rarities(identity_batch_world):
    manor, pool, _family, common = identity_batch_world
    fallback = GuestTemplate.objects.create(
        key="identity_black_fallback", name="普通门客", rarity=GuestRarity.BLACK, archetype="military"
    )
    excluded_ids = set()
    candidates = build_candidate_batch(
        manor=manor,
        pool=pool,
        pool_entries=[],
        resolved_draw_count=2,
        excluded_ids=excluded_ids,
        rng=random.Random(1),
        choose_template_from_entries=partial(choose_template_from_entries, rarity=GuestRarity.GRAY),
        templates_by_rarity={GuestRarity.GRAY: [common], GuestRarity.BLACK: [fallback]},
        hermit_templates=[],
        generate_random_name=lambda _rng: "普通门客",
        non_repeatable_rarities=frozenset({GuestRarity.GRAY}),
    )

    assert [candidate.template_id for candidate in candidates] == [common.pk, fallback.pk]
    assert excluded_ids == {common.pk}


@pytest.mark.django_db
def test_batch_family_mapping_keeps_dynamic_sources_cycles_and_repeatable_semantics(identity_batch_world):
    _manor, _pool, family, common = identity_batch_world
    converted = GuestTemplate.objects.create(
        key=f"guest_rarity_conversion_purple_{family[0].pk}",
        name="升阶原身",
        rarity=GuestRarity.PURPLE,
        archetype="military",
    )
    cycle = GuestTemplate.objects.create(key="identity_cycle", name="异常循环", rarity=GuestRarity.BLUE)
    cycle.key = f"guest_rarity_conversion_blue_{cycle.pk}"
    cycle.save(update_fields=["key"])

    mapping = get_non_repeatable_family_template_ids_by_id([converted.pk, common.pk, cycle.pk, 999999999])
    assert mapping[converted.pk] == {converted.pk, *(template.pk for template in family)}
    assert mapping[common.pk] == set()
    assert mapping[cycle.pk] == {cycle.pk}
    assert mapping[999999999] == set()
