from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from guests.management.commands.load_guest_templates import Command
from guests.models import GuestTemplate, RecruitmentPool, RecruitmentPoolEntry, Skill, SkillBook


class _Recorder:
    def __init__(self, sink: list[str] | None = None) -> None:
        self.sink = sink if sink is not None else []

    def write(self, message: Any) -> None:
        self.sink.append(str(message))


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


def test_default_special_yaml_contains_task_specific_enemy_templates() -> None:
    command = Command()
    payload = command._load_heroes_payload("")

    green_keys = {entry["key"] for entry in payload.get("green", [])}
    blue_keys = {entry["key"] for entry in payload.get("blue", [])}
    orange_keys = {entry["key"] for entry in payload.get("orange", [])}

    assert "task_huashan_jianwang" in green_keys
    assert "task_huashan_jianjing" in green_keys
    assert "task_huashan_audience_a" in green_keys
    assert "task_wulong_bandit_chief_zuanshanbao" in blue_keys
    assert "task_wulong_bandit_chief_zuoshandiao" in blue_keys
    assert "task_shiren_xiaomimi" in blue_keys
    assert "task_shiren_xuanyuansanguang" in blue_keys
    assert "task_shiren_tiezhan" in blue_keys
    assert "task_shiren_luoshixiongdi" in blue_keys
    assert "task_barbarian_chanyu" in orange_keys


@pytest.mark.django_db
def test_load_guest_templates_imports_default_special_task_heroes(tmp_path: Path) -> None:
    call_command(
        "load_guest_templates",
        file=str(Path(settings.BASE_DIR) / "data" / "guest_templates.yaml"),
        heroes_dir=str(Path(settings.BASE_DIR) / "data" / "guests"),
        skip_images=True,
        verbosity=0,
    )

    tiger = GuestTemplate.objects.get(key="task_jingyang_tiger")
    assert tiger.name == "猛虎"

    jianwang = GuestTemplate.objects.get(key="task_huashan_jianwang")
    assert jianwang.name == "贱王之王"
    assert jianwang.rarity == "green"
    assert jianwang.recruitable is False

    jianjing = GuestTemplate.objects.get(key="task_huashan_jianjing")
    assert jianjing.name == "顶级贱精"
    assert jianjing.rarity == "green"
    assert jianjing.recruitable is False

    audience = GuestTemplate.objects.get(key="task_huashan_audience_a")
    assert audience.name == "观众甲"
    assert audience.rarity == "green"
    assert audience.recruitable is False

    bandit_a = GuestTemplate.objects.get(key="task_wulong_bandit_chief_zuanshanbao")
    assert bandit_a.name == "钻山豹"
    assert bandit_a.rarity == "blue"
    assert bandit_a.recruitable is False

    bandit_b = GuestTemplate.objects.get(key="task_wulong_bandit_chief_zuoshandiao")
    assert bandit_b.name == "座山雕"
    assert bandit_b.rarity == "blue"
    assert bandit_b.recruitable is False

    xiaomimi = GuestTemplate.objects.get(key="task_shiren_xiaomimi")
    assert xiaomimi.name == "萧咪咪"
    assert xiaomimi.rarity == "blue"
    assert xiaomimi.recruitable is False

    xuanyuan = GuestTemplate.objects.get(key="task_shiren_xuanyuansanguang")
    assert xuanyuan.name == "轩辕三光"
    assert xuanyuan.rarity == "blue"
    assert xuanyuan.recruitable is False

    tiezhan = GuestTemplate.objects.get(key="task_shiren_tiezhan")
    assert tiezhan.name == "铁战"
    assert tiezhan.rarity == "blue"
    assert tiezhan.recruitable is False

    luoshi = GuestTemplate.objects.get(key="task_shiren_luoshixiongdi")
    assert luoshi.name == "罗氏兄弟"
    assert luoshi.rarity == "blue"
    assert luoshi.recruitable is False

    template = GuestTemplate.objects.get(key="task_barbarian_chanyu")
    assert template.name == "单于"
    assert template.rarity == "orange"
    assert template.recruitable is False


@pytest.mark.django_db
def test_sync_template_avatar_programming_error_bubbles_up(tmp_path: Path, monkeypatch) -> None:
    command = Command()
    command.stdout = cast(Any, _Recorder())
    template = GuestTemplate.objects.create(
        key="avatar_bug_tpl",
        name="头像契约错误模板",
        archetype="civil",
        rarity="gray",
    )
    avatar_path = tmp_path / "avatar.png"
    avatar_path.write_bytes(b"not-used")

    monkeypatch.setattr(
        command,
        "_ensure_avatar_saved",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken avatar contract")),
    )

    with pytest.raises(AssertionError, match="broken avatar contract"):
        command._sync_template_avatar(
            template,
            {"avatar": "avatar.png"},
            False,
            tmp_path,
            {},
            {},
            1,
        )


def test_load_heroes_from_dir_recoverable_command_error_degrades(tmp_path: Path) -> None:
    command = Command()
    messages: list[str] = []
    command.stdout = cast(Any, _Recorder(messages))

    (tmp_path / "good.json").write_text(json.dumps({"heroes": {"green": [{"key": "hero_ok"}]}}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")

    def _load_payload(path: Path) -> dict:
        if path.name == "bad.json":
            raise CommandError("broken hero roster file")
        return json.loads(path.read_text(encoding="utf-8"))

    merged = command._load_heroes_from_dir(tmp_path, _load_payload)

    assert merged == {"green": [{"key": "hero_ok"}]}
    assert any("Failed to load bad.json: broken hero roster file" in message for message in messages)


def test_load_heroes_from_dir_programming_error_bubbles_up(tmp_path: Path) -> None:
    command = Command()
    command.stdout = cast(Any, _Recorder())

    (tmp_path / "broken.json").write_text("{}", encoding="utf-8")

    def _load_payload(_path: Path) -> dict:
        raise AssertionError("broken hero roster contract")

    with pytest.raises(AssertionError, match="broken hero roster contract"):
        command._load_heroes_from_dir(tmp_path, _load_payload)
