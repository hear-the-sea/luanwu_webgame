import random
from pathlib import Path

import pytest
import yaml

from core.exceptions import RecruitmentCandidateStateError
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool
from guests.services.recruitment_guests import bulk_finalize_candidates, finalize_candidate
from guests.services.recruitment_templates import _get_recruitable_templates_by_rarity, clear_template_cache

EXCLUSIVE_GUESTS = {
    "hero_jinyong_0215": ("小昭", "jinyong.yaml"),
    "hero_yang_zheng_purple": ("杨铮", "chiyou_exchange.yaml"),
    "hero_di_qinglin": ("狄青麟", "chiyou_exchange.yaml"),
    "hero_heishan_laoyao": ("黑山老妖", "surname_li.yaml"),
    "hero_li_lizhen": ("李利贞", "surname_li.yaml"),
    "hist_sljnbc_0012": ("赵云", "history_sljnbc_01.yaml"),
}


@pytest.mark.parametrize("key", EXCLUSIVE_GUESTS)
def test_exclusive_guest_roster_disables_ordinary_recruitment(key):
    name, filename = EXCLUSIVE_GUESTS[key]
    payload = yaml.safe_load((Path("data/guests") / filename).read_text())
    rows = [row for group in payload["heroes"].values() for row in group if row["key"] == key]
    assert len(rows) == 1
    assert rows[0]["name"] == name
    assert rows[0]["recruitable"] is False


@pytest.fixture
def exclusive_world(db, django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="exclusive_recruitment"))
    pool = RecruitmentPool.objects.create(key="exclusive_recruitment", name="招募测试", draw_count=1)
    templates = []
    for key, (name, _) in EXCLUSIVE_GUESTS.items():
        template, _ = GuestTemplate.objects.update_or_create(
            key=key, defaults={"name": name, "rarity": "purple", "archetype": "civil", "recruitable": False}
        )
        templates.append(template)
    ordinary = GuestTemplate.objects.create(
        key="ordinary_exclusive_recruitment", name="普通门客", rarity="purple", archetype="civil"
    )
    return manor, pool, templates, ordinary


def test_disabling_recruitment_takes_effect_even_with_old_template_cache(exclusive_world):
    _, _, templates, ordinary = exclusive_world
    GuestTemplate.objects.filter(pk__in=[t.pk for t in templates]).update(recruitable=True, is_world_unique=False)
    clear_template_cache()
    before = _get_recruitable_templates_by_rarity()
    assert {t.pk for t in templates}.issubset({t.pk for t in before["purple"]})
    GuestTemplate.objects.filter(pk__in=[t.pk for t in templates]).update(recruitable=False)
    after = _get_recruitable_templates_by_rarity()
    choices = {t.pk for group in after.values() for t in group}
    assert ordinary.pk in choices
    assert not choices.intersection({t.pk for t in templates})


def candidate(manor, pool, template):
    return RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        rarity=template.rarity,
        archetype=template.archetype,
        display_name=template.name,
    )


@pytest.mark.parametrize("key", EXCLUSIVE_GUESTS)
def test_old_candidate_cannot_finalize_a_disabled_guest(exclusive_world, key):
    manor, pool, templates, _ = exclusive_world
    template = next(t for t in templates if t.key == key)
    pending = candidate(manor, pool, template)
    with pytest.raises(RecruitmentCandidateStateError, match="无法普通招募"):
        finalize_candidate(pending)
    assert not Guest.objects.filter(manor=manor).exists()
    assert RecruitmentCandidate.objects.filter(pk=pending.pk).exists()


def test_bulk_recruitment_rejects_exclusive_guests_and_keeps_ordinary_candidate(exclusive_world):
    manor, pool, templates, ordinary = exclusive_world
    blocked = [candidate(manor, pool, t) for t in templates]
    pending = candidate(manor, pool, ordinary)
    candidates = [*blocked, pending]
    random.Random(1).shuffle(candidates)
    guests, failed = bulk_finalize_candidates(candidates)
    assert [guest.template_id for guest in guests] == [ordinary.pk]
    assert {c.pk for c in failed} == {c.pk for c in blocked}
    assert Guest.objects.filter(manor=manor).count() == 1
    assert RecruitmentCandidate.objects.filter(manor=manor).count() == 6
