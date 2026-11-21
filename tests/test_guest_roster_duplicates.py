"""门客图鉴不应同时保留同一人物的多个模板。"""

import importlib
from pathlib import Path

import pytest
import yaml
from django.apps import apps
from django.conf import settings

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool, RecruitmentPoolEntry

pytestmark = pytest.mark.django_db


def _iter_entries(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _iter_entries(value)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and item.get("key"):
                yield item
            else:
                yield from _iter_entries(item)


def _roster_entries():
    roster_dir = Path(settings.BASE_DIR) / "data" / "guests"
    for path in sorted(roster_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in _iter_entries(payload):
            yield path, entry


def _roster_rarity(key: str) -> str | None:
    roster_dir = Path(settings.BASE_DIR) / "data" / "guests"
    for path in sorted(roster_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for rarity, entries in (payload.get("heroes") or {}).items():
            if any(entry.get("key") == key for entry in entries if isinstance(entry, dict)):
                return rarity
    return None


def test_cheng_yaojin_is_the_only_template_for_this_person():
    entries = list(_roster_entries())

    assert any(entry["key"] == "hero_cheng_yaojin" for _, entry in entries)
    assert all(entry["key"] != "hist_suitang_0057_chengzhijie" for _, entry in entries)
    assert all(entry.get("name") != "程知节" for _, entry in entries)
    assert _roster_rarity("hero_cheng_yaojin") == "purple"
    assert _roster_rarity("hist_suitang_0030_zhangsunwujii") == "purple"


def test_merge_migration_repoints_existing_guests(user_factory):
    migration = importlib.import_module("guests.migrations.0096_merge_cheng_zhijie_into_cheng_yaojin")
    canonical = GuestTemplate.objects.create(
        key="hero_cheng_yaojin", name="程咬金", archetype="military", rarity="blue"
    )
    duplicate = GuestTemplate.objects.create(
        key="hist_suitang_0057_chengzhijie", name="程知节", archetype="military", rarity="blue"
    )
    guest = Guest.objects.create(manor=ensure_manor(user_factory()), template=duplicate)

    migration.merge_duplicate_template(apps, None)

    guest.refresh_from_db()
    assert guest.template_id == canonical.pk
    assert not GuestTemplate.objects.filter(pk=duplicate.pk).exists()


@pytest.mark.parametrize(
    ("duplicate_key", "canonical_key"),
    [
        ("hist_sljnbc_0380", "hist_sljnbc_0381"),
        ("hist_sljnbc_0382", "hist_sljnbc_0383"),
        ("hist_suitang_0266", "hist_suitang_0055_yuchigong"),
    ],
)
def test_duplicate_template_migration_repoints_existing_guests(duplicate_key, canonical_key, user_factory):
    migration = importlib.import_module("guests.migrations.0097_merge_duplicate_guest_templates")
    canonical = GuestTemplate.objects.create(key=canonical_key, name="保留", archetype="military", rarity="blue")
    duplicate = GuestTemplate.objects.create(key=duplicate_key, name="重复", archetype="military", rarity="green")
    guest = Guest.objects.create(manor=ensure_manor(user_factory()), template=duplicate)

    migration.merge_duplicate_templates(apps, None)

    guest.refresh_from_db()
    assert guest.template_id == canonical.pk
    assert not GuestTemplate.objects.filter(pk=duplicate.pk).exists()


def test_duplicate_template_migration_syncs_candidate_snapshot(user_factory):
    migration = importlib.import_module("guests.migrations.0097_merge_duplicate_guest_templates")
    canonical = GuestTemplate.objects.create(key="hist_sljnbc_0381", name="袁术", archetype="military", rarity="blue")
    duplicate = GuestTemplate.objects.create(key="hist_sljnbc_0380", name="袁术", archetype="civil", rarity="green")
    pool = RecruitmentPool.objects.create(key="test_pool", name="测试卡池")
    candidate = RecruitmentCandidate.objects.create(
        manor=ensure_manor(user_factory()),
        pool=pool,
        template=duplicate,
        display_name="旧名",
        rarity="green",
        archetype="civil",
    )

    migration.merge_duplicate_templates(apps, None)

    candidate.refresh_from_db()
    assert candidate.template_id == canonical.pk
    assert candidate.rarity == "blue"
    assert candidate.archetype == "military"
    assert candidate.display_name == "袁术"


@pytest.mark.parametrize(
    "removed_key",
    [
        "hist_xianqin_0346",  # 熊胜 = 白公胜
        "hist_xianqin_0347",  # 郧公辛 = 斗辛
        "hist_xianqin_0327",  # 绛灌 = 周勃 + 灌婴，不是单一人物
    ],
)
def test_removed_duplicate_xianqin_templates_are_gone(removed_key):
    entries = list(_roster_entries())

    assert all(entry["key"] != removed_key for _, entry in entries)


@pytest.mark.parametrize(
    ("duplicate_key", "canonical_key"),
    [
        ("hist_xianqin_0346", "hist_xianqin_0335"),
        ("hist_xianqin_0347", "hist_xianqin_0349"),
        ("hist_xianqin_0327", "hist_qinhan_0027"),
    ],
)
def test_xianqin_duplicate_migration_repoints_existing_guests(duplicate_key, canonical_key, user_factory):
    migration = importlib.import_module("guests.migrations.0099_merge_duplicate_xianqin_guests")
    canonical = GuestTemplate.objects.create(key=canonical_key, name="保留", archetype="military", rarity="green")
    duplicate = GuestTemplate.objects.create(key=duplicate_key, name="重复", archetype="military", rarity="green")
    guest = Guest.objects.create(manor=ensure_manor(user_factory()), template=duplicate)

    migration.merge_duplicate_templates(apps, None)

    guest.refresh_from_db()
    assert guest.template_id == canonical.pk
    assert not GuestTemplate.objects.filter(pk=duplicate.pk).exists()


def test_liuxiahui_is_the_only_template_for_this_person():
    entries = list(_roster_entries())

    assert all(entry["key"] != "hist_xianqin_0506" for _, entry in entries)
    assert any(entry["key"] == "hist_xianqin_0383" and entry.get("name") == "柳下惠" for _, entry in entries)


def test_liuxiahui_merge_migration_repoints_existing_guests(user_factory):
    migration = importlib.import_module("guests.migrations.0100_merge_liuxiahui_into_zhanhuo")
    canonical = GuestTemplate.objects.create(key="hist_xianqin_0383", name="柳下惠", archetype="civil", rarity="blue")
    duplicate = GuestTemplate.objects.create(key="hist_xianqin_0506", name="柳下惠", archetype="civil", rarity="blue")
    guest = Guest.objects.create(manor=ensure_manor(user_factory()), template=duplicate)

    migration.merge_duplicate_template(apps, None)

    guest.refresh_from_db()
    assert guest.template_id == canonical.pk
    assert not GuestTemplate.objects.filter(pk=duplicate.pk).exists()


def test_duplicate_template_migration_merges_pool_entries():
    migration = importlib.import_module("guests.migrations.0097_merge_duplicate_guest_templates")
    canonical = GuestTemplate.objects.create(key="hist_sljnbc_0381", name="袁术", archetype="military", rarity="blue")
    duplicate = GuestTemplate.objects.create(key="hist_sljnbc_0380", name="袁术", archetype="military", rarity="green")
    pool = RecruitmentPool.objects.create(key="test_pool", name="测试卡池")
    RecruitmentPoolEntry.objects.create(pool=pool, template=canonical, weight=3)
    RecruitmentPoolEntry.objects.create(pool=pool, template=duplicate, weight=2)

    migration.merge_duplicate_templates(apps, None)

    entries = RecruitmentPoolEntry.objects.filter(pool=pool, template=canonical)
    assert entries.count() == 1
    assert entries.get().weight == 5


def test_merge_migration_repoints_jail_prisoners_and_syncs_0096_candidates_and_pools(user_factory):
    from gameplay.models import JailPrisoner

    migration = importlib.import_module("guests.migrations.0096_merge_cheng_zhijie_into_cheng_yaojin")
    canonical = GuestTemplate.objects.create(
        key="hero_cheng_yaojin", name="程咬金", archetype="military", rarity="purple"
    )
    duplicate = GuestTemplate.objects.create(
        key="hist_suitang_0057_chengzhijie", name="程知节", archetype="military", rarity="blue"
    )
    captor = ensure_manor(user_factory())
    victim = ensure_manor(user_factory())
    prisoner = JailPrisoner.objects.create(
        captor=captor,
        original_manor=victim,
        guest_template=duplicate,
        original_guest_name="程知节",
        captured_loyalty=50,
    )
    pool = RecruitmentPool.objects.create(key="cheng_pool", name="测试卡池")
    RecruitmentPoolEntry.objects.create(pool=pool, template=canonical, weight=4)
    RecruitmentPoolEntry.objects.create(pool=pool, template=duplicate, weight=3)
    candidate = RecruitmentCandidate.objects.create(
        manor=captor,
        pool=pool,
        template=duplicate,
        display_name="程知节",
        rarity="blue",
        archetype="military",
    )

    migration.merge_duplicate_template(apps, None)

    prisoner.refresh_from_db()
    candidate.refresh_from_db()
    assert prisoner.guest_template_id == canonical.pk
    assert candidate.template_id == canonical.pk
    assert candidate.display_name == "程咬金"
    assert candidate.rarity == "purple"
    entries = RecruitmentPoolEntry.objects.filter(pool=pool, template=canonical)
    assert entries.count() == 1
    assert entries.get().weight == 7
    assert not GuestTemplate.objects.filter(pk=duplicate.pk).exists()


def test_merge_migration_transfers_world_unique_serving_state_without_one_to_one_conflict(user_factory):
    from guests.models import WorldUniqueGuest

    migration = importlib.import_module("guests.migrations.0097_merge_duplicate_guest_templates")
    canonical = GuestTemplate.objects.create(
        key="hist_suitang_0055_yuchigong", name="尉迟恭", archetype="military", rarity="purple", is_world_unique=True
    )
    duplicate = GuestTemplate.objects.create(
        key="hist_suitang_0266", name="尉迟敬德", archetype="civil", rarity="blue", is_world_unique=True
    )
    manor = ensure_manor(user_factory())
    guest = Guest.objects.create(manor=manor, template=duplicate)
    canonical_state = WorldUniqueGuest.objects.create(template=canonical, status=WorldUniqueGuest.Status.WILD)
    WorldUniqueGuest.objects.create(
        template=duplicate,
        status=WorldUniqueGuest.Status.SERVING,
        owner_manor=manor,
        owner_guest=guest,
    )

    migration.merge_duplicate_templates(apps, None)

    canonical_state.refresh_from_db()
    guest.refresh_from_db()
    assert guest.template_id == canonical.pk
    assert canonical_state.status == WorldUniqueGuest.Status.SERVING
    assert canonical_state.owner_manor_id == manor.pk
    assert canonical_state.owner_guest_id == guest.pk
    assert not GuestTemplate.objects.filter(pk=duplicate.pk).exists()
