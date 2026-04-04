from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate, RecruitmentPool, RecruitmentPoolEntry, Skill, SkillBook


@pytest.mark.django_db
def test_load_guest_templates_links_existing_skill_and_filters_invalid_pool_entries(tmp_path: Path) -> None:
    Skill.objects.create(key="legacy_skill", name="旧技能")

    payload = {
        "templates": [
            {
                "key": "tpl_loader_a",
                "name": "模板甲",
                "archetype": "civil",
                "rarity": "gray",
                "skills": ["legacy_skill"],
            }
        ],
        "skill_books": [
            {
                "key": "book_loader_a",
                "name": "秘籍甲",
                "skill": "legacy_skill",
            }
        ],
        "pools": [
            {
                "key": "pool_loader_a",
                "name": "测试卡池",
                "entries": [
                    {"template": "tpl_loader_a", "weight": 7},
                    {"rarity": "green", "archetype": "military", "weight": 3},
                    {"template": "tpl_not_found", "weight": 9},
                    {"weight": 1},
                ],
            }
        ],
    }

    main_file = tmp_path / "guest_templates.json"
    main_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills_empty.json"
    skills_file.write_text("{}", encoding="utf-8")

    heroes_dir = tmp_path / "heroes"
    heroes_dir.mkdir()

    call_command(
        "load_guest_templates",
        file=str(main_file),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    template = GuestTemplate.objects.get(key="tpl_loader_a")
    linked_skill = template.initial_skills.get()
    assert linked_skill.key == "legacy_skill"

    book = SkillBook.objects.get(key="book_loader_a")
    assert book.skill.key == "legacy_skill"

    pool = RecruitmentPool.objects.get(key="pool_loader_a")
    entries = list(RecruitmentPoolEntry.objects.filter(pool=pool).order_by("weight"))
    assert len(entries) == 2
    assert entries[0].template is None
    assert entries[0].rarity == "green"
    assert entries[1].template is not None
    assert entries[1].template.key == "tpl_loader_a"


@pytest.mark.django_db
def test_load_guest_templates_replaces_pool_entries_on_reimport(tmp_path: Path) -> None:
    payload_v1 = {
        "templates": [
            {
                "key": "tpl_loader_b",
                "name": "模板乙",
                "archetype": "military",
                "rarity": "green",
            }
        ],
        "pools": [
            {
                "key": "pool_loader_b",
                "name": "重载卡池",
                "entries": [
                    {"template": "tpl_loader_b", "weight": 5},
                ],
            }
        ],
    }

    payload_v2 = {
        "templates": payload_v1["templates"],
        "pools": [
            {
                "key": "pool_loader_b",
                "name": "重载卡池",
                "entries": [
                    {"rarity": "blue", "archetype": "civil", "weight": 2},
                ],
            }
        ],
    }

    file_v1 = tmp_path / "guest_templates_v1.json"
    file_v1.write_text(json.dumps(payload_v1, ensure_ascii=False), encoding="utf-8")

    file_v2 = tmp_path / "guest_templates_v2.json"
    file_v2.write_text(json.dumps(payload_v2, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills_empty.json"
    skills_file.write_text("{}", encoding="utf-8")

    heroes_dir = tmp_path / "heroes"
    heroes_dir.mkdir()

    call_command(
        "load_guest_templates",
        file=str(file_v1),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )
    call_command(
        "load_guest_templates",
        file=str(file_v2),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    pool = RecruitmentPool.objects.get(key="pool_loader_b")
    entries = list(RecruitmentPoolEntry.objects.filter(pool=pool))
    assert len(entries) == 1
    assert entries[0].template is None
    assert entries[0].rarity == "blue"
    assert entries[0].archetype == "civil"


@pytest.mark.django_db
def test_load_guest_templates_pool_cooldown_defaults_to_zero_when_missing(tmp_path: Path) -> None:
    payload = {
        "templates": [
            {
                "key": "tpl_loader_c",
                "name": "模板丙",
                "archetype": "civil",
                "rarity": "gray",
            }
        ],
        "pools": [
            {
                "key": "pool_loader_c",
                "name": "无冷却配置卡池",
                "entries": [
                    {"template": "tpl_loader_c", "weight": 1},
                ],
            }
        ],
    }

    main_file = tmp_path / "guest_templates_c.json"
    main_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills_empty.json"
    skills_file.write_text("{}", encoding="utf-8")

    heroes_dir = tmp_path / "heroes"
    heroes_dir.mkdir()

    call_command(
        "load_guest_templates",
        file=str(main_file),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    pool = RecruitmentPool.objects.get(key="pool_loader_c")
    assert pool.cooldown_seconds == 0


@pytest.mark.django_db
def test_load_guest_templates_removes_records_not_in_latest_payload(tmp_path: Path) -> None:
    payload_v1 = {
        "templates": [
            {
                "key": "tpl_loader_keep",
                "name": "保留模板",
                "archetype": "civil",
                "rarity": "gray",
                "skills": ["skill_loader_keep"],
            },
            {
                "key": "tpl_loader_drop",
                "name": "删除模板",
                "archetype": "military",
                "rarity": "green",
                "skills": ["skill_loader_drop"],
            },
        ],
        "skills": [
            {"key": "skill_loader_keep", "name": "保留技能"},
            {"key": "skill_loader_drop", "name": "删除技能"},
        ],
        "skill_books": [
            {"key": "book_loader_keep", "name": "保留秘籍", "skill": "skill_loader_keep"},
            {"key": "book_loader_drop", "name": "删除秘籍", "skill": "skill_loader_drop"},
        ],
        "pools": [
            {
                "key": "pool_loader_keep",
                "name": "保留卡池",
                "entries": [{"template": "tpl_loader_keep", "weight": 1}],
            },
            {
                "key": "pool_loader_drop",
                "name": "删除卡池",
                "entries": [{"template": "tpl_loader_drop", "weight": 1}],
            },
        ],
    }

    payload_v2 = {
        "templates": [
            {
                "key": "tpl_loader_keep",
                "name": "保留模板",
                "archetype": "civil",
                "rarity": "gray",
                "skills": ["skill_loader_keep"],
            },
        ],
        "skills": [
            {"key": "skill_loader_keep", "name": "保留技能"},
        ],
        "skill_books": [
            {"key": "book_loader_keep", "name": "保留秘籍", "skill": "skill_loader_keep"},
        ],
        "pools": [
            {
                "key": "pool_loader_keep",
                "name": "保留卡池",
                "entries": [{"template": "tpl_loader_keep", "weight": 1}],
            },
        ],
    }

    file_v1 = tmp_path / "guest_templates_full_rebuild_v1.json"
    file_v1.write_text(json.dumps(payload_v1, ensure_ascii=False), encoding="utf-8")

    file_v2 = tmp_path / "guest_templates_full_rebuild_v2.json"
    file_v2.write_text(json.dumps(payload_v2, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills_empty.json"
    skills_file.write_text("{}", encoding="utf-8")

    heroes_dir = tmp_path / "heroes"
    heroes_dir.mkdir()

    call_command(
        "load_guest_templates",
        file=str(file_v1),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )
    call_command(
        "load_guest_templates",
        file=str(file_v2),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    assert set(GuestTemplate.objects.values_list("key", flat=True)) == {"tpl_loader_keep"}
    assert set(Skill.objects.values_list("key", flat=True)) == {"skill_loader_keep"}
    assert set(SkillBook.objects.values_list("key", flat=True)) == {"book_loader_keep"}
    assert set(RecruitmentPool.objects.values_list("key", flat=True)) == {"pool_loader_keep"}


@pytest.mark.django_db
def test_load_guest_templates_reimport_syncs_existing_guest_current_hp_for_base_hp_changes(
    tmp_path: Path,
    django_user_model,
) -> None:
    payload_v1 = {
        "templates": [
            {
                "key": "tpl_loader_hp_sync",
                "name": "血量同步模板",
                "archetype": "military",
                "rarity": "gray",
                "base_hp": 1200,
            }
        ],
    }
    payload_v2 = {
        "templates": [
            {
                "key": "tpl_loader_hp_sync",
                "name": "血量同步模板",
                "archetype": "military",
                "rarity": "gray",
                "base_hp": 1000,
            }
        ],
    }

    file_v1 = tmp_path / "guest_templates_hp_sync_v1.json"
    file_v1.write_text(json.dumps(payload_v1, ensure_ascii=False), encoding="utf-8")

    file_v2 = tmp_path / "guest_templates_hp_sync_v2.json"
    file_v2.write_text(json.dumps(payload_v2, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills_empty.json"
    skills_file.write_text("{}", encoding="utf-8")

    heroes_dir = tmp_path / "heroes"
    heroes_dir.mkdir()

    call_command(
        "load_guest_templates",
        file=str(file_v1),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    template = GuestTemplate.objects.get(key="tpl_loader_hp_sync")
    user = django_user_model.objects.create_user(username="load_guest_templates_hp_sync", password="pass12345")
    manor = ensure_manor(user)

    full_guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=10,
        force=100,
        intellect=80,
        defense_stat=80,
    )
    half_guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=10,
        force=100,
        intellect=80,
        defense_stat=80,
        current_hp=full_guest.max_hp // 2,
    )

    assert full_guest.current_hp == 5200
    assert half_guest.current_hp == 2600

    call_command(
        "load_guest_templates",
        file=str(file_v2),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    full_guest.refresh_from_db()
    half_guest.refresh_from_db()

    assert full_guest.max_hp == 5000
    assert half_guest.max_hp == 5000
    assert full_guest.current_hp == 5000
    assert half_guest.current_hp == 2500


@pytest.mark.django_db
def test_load_guest_templates_reimport_clears_injured_status_when_hp_sync_reaches_full(
    tmp_path: Path,
    django_user_model,
) -> None:
    payload_v1 = {
        "templates": [
            {
                "key": "tpl_loader_hp_status_sync",
                "name": "血量状态同步模板",
                "archetype": "military",
                "rarity": "gray",
                "base_hp": 1200,
            }
        ],
    }
    payload_v2 = {
        "templates": [
            {
                "key": "tpl_loader_hp_status_sync",
                "name": "血量状态同步模板",
                "archetype": "military",
                "rarity": "gray",
                "base_hp": 1000,
            }
        ],
    }

    file_v1 = tmp_path / "guest_templates_hp_status_sync_v1.json"
    file_v1.write_text(json.dumps(payload_v1, ensure_ascii=False), encoding="utf-8")

    file_v2 = tmp_path / "guest_templates_hp_status_sync_v2.json"
    file_v2.write_text(json.dumps(payload_v2, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills_empty.json"
    skills_file.write_text("{}", encoding="utf-8")

    heroes_dir = tmp_path / "heroes"
    heroes_dir.mkdir()

    call_command(
        "load_guest_templates",
        file=str(file_v1),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    template = GuestTemplate.objects.get(key="tpl_loader_hp_status_sync")
    user = django_user_model.objects.create_user(username="load_guest_templates_hp_status_sync", password="pass12345")
    manor = ensure_manor(user)

    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=10,
        force=100,
        intellect=80,
        defense_stat=80,
        current_hp=5200,
        status=GuestStatus.INJURED,
    )

    call_command(
        "load_guest_templates",
        file=str(file_v2),
        skills_file=str(skills_file),
        heroes_dir=str(heroes_dir),
        skip_images=True,
        verbosity=0,
    )

    guest.refresh_from_db()

    assert guest.max_hp == 5000
    assert guest.current_hp == 5000
    assert guest.status == GuestStatus.IDLE
