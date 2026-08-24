from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from guests.management.commands.load_guest_templates import Command
from guests.models import GuestTemplate, Skill, SkillKind


def _hero_entry_by_key(payload: dict[str, list[dict[str, object]]], key: str) -> dict[str, object]:
    for entries in payload.values():
        for entry in entries:
            if entry["key"] == key:
                return entry
    raise AssertionError(f"missing hero entry: {key}")


def test_default_special_yaml_contains_task_specific_enemy_templates() -> None:
    command = Command()
    payload = command._load_heroes_payload("")

    green_keys = {entry["key"] for entry in payload.get("green", [])}
    blue_keys = {entry["key"] for entry in payload.get("blue", [])}
    purple_keys = {entry["key"] for entry in payload.get("purple", [])}
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
    assert "guild_wulinzhai_torch_archer" in blue_keys
    assert "guild_wulinzhai_zhai_zhu" in purple_keys
    assert "guild_bloodflag_head_escort" in purple_keys
    assert "guild_blackwind_iron_guard" in purple_keys
    assert "guild_blackwind_gate_general" in orange_keys
    assert "task_barbarian_chanyu" in orange_keys
    assert "task_guandu_yuanshao" in orange_keys
    assert "task_hulao_lvbu" in orange_keys


def test_load_guest_templates_merges_default_arena_coop_special_skills(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / "repo"
    data_dir = base_dir / "data"
    guests_dir = data_dir / "guests"
    data_dir.mkdir(parents=True)
    guests_dir.mkdir()

    (data_dir / "guest_skills.yaml").write_text(
        "skills:\n  - key: base_skill\n    name: 基础技能\n",
        encoding="utf-8",
    )
    (data_dir / "arena_coop_special_skills.yaml").write_text(
        "skills:\n  - key: gl_top_nine_yang_guard\n    name: 九阳护体\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "BASE_DIR", str(base_dir))

    payload = Command()._load_skills_payload("")

    keys = {entry["key"] for entry in payload["skills"]}
    assert "base_skill" in keys
    assert "gl_top_nine_yang_guard" in keys


def test_load_guest_templates_merges_default_guild_mission_special_skills() -> None:
    payload = Command()._load_skills_payload("")

    skills = {entry["key"]: entry for entry in payload["skills"]}

    assert "guild_wulinzhai_shadow_raid" in skills
    assert skills["guild_wulinzhai_night_signal"]["kind"] == "passive"
    assert skills["guild_wulinzhai_night_signal"]["passive_config"]
    assert skills["guild_bloodflag_convoy_command"]["kind"] == "passive"
    assert skills["guild_bloodflag_convoy_command"]["passive_config"]
    assert skills["guild_blackwind_battle_standard"]["kind"] == "passive"
    assert skills["guild_blackwind_battle_standard"]["passive_config"]


def test_bloodthirsty_fury_triggers_before_its_action() -> None:
    payload = Command()._load_skills_payload("")
    skills = {entry["key"]: entry for entry in payload["skills"]}

    trigger = skills["bloodthirsty_fury"]["passive_config"]["triggers"][0]

    assert trigger["timing"] == "action_before"


def test_default_special_yaml_contains_guild_mission_template_skill_bindings() -> None:
    payload = Command()._load_heroes_payload("")

    wulinzhai_zhai_zhu = _hero_entry_by_key(payload, "guild_wulinzhai_zhai_zhu")
    bloodflag_head_escort = _hero_entry_by_key(payload, "guild_bloodflag_head_escort")
    blackwind_gate_general = _hero_entry_by_key(payload, "guild_blackwind_gate_general")

    assert "guild_wulinzhai_shadow_raid" in wulinzhai_zhai_zhu["skills"]
    assert "guild_bloodflag_convoy_command" in bloodflag_head_escort["skills"]
    assert "guild_blackwind_battle_standard" in blackwind_gate_general["skills"]


def test_wanxian_key_bosses_use_exclusive_skills() -> None:
    heroes_payload = Command()._load_heroes_payload("")
    skills_payload = Command()._load_skills_payload("")

    skills = {entry["key"]: entry for entry in skills_payload["skills"]}
    expected_bindings = {
        "task_wanxian_qingsuan_fuzhao": "wanxian_qingsuan_fuzhao",
        "task_wanxian_shier_xianyin": "wanxian_shier_xianyin",
        "task_wanxian_randeng": "wanxian_randeng_dengyan",
        "task_wanxian_jiang_ziya": "wanxian_fengbang_zhiming",
        "task_wanxian_dashenbian": "wanxian_dashenbian",
        "task_wanxian_yuxu_tianfa": "wanxian_yuxu_tianfa",
    }

    for boss_key, skill_key in expected_bindings.items():
        boss = _hero_entry_by_key(heroes_payload, boss_key)
        assert skill_key in boss["skills"]

        skill = skills[skill_key]
        assert len(skill["name"]) == 4
        assert skill["kind"] == "active"
        assert skill["rarity"] == "purple"
        assert skill["base_probability"] >= 0.7
        assert skill["targets"] >= 2
        assert skill["damage_formula"]["base"] >= 2800


@pytest.mark.django_db
def test_load_guest_templates_imports_passive_config(tmp_path: Path) -> None:
    main_file = tmp_path / "guest_templates.json"
    main_file.write_text(json.dumps({"templates": [], "pools": []}, ensure_ascii=False), encoding="utf-8")

    skills_file = tmp_path / "skills.yaml"
    skills_file.write_text(
        """
skills:
  - key: passive_loader_skill
    name: 测试被动
    kind: passive
    passive_config:
      triggers:
        - timing: action_before
          conditions:
            hp_ratio_lte: 0.5
          effects:
            - type: heal_ratio
              value: 0.1
              max_hp_based: true
              log: true
              log_name: 测试被动
""",
        encoding="utf-8",
    )

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

    skill = Skill.objects.get(key="passive_loader_skill")
    assert skill.kind == SkillKind.PASSIVE
    assert skill.passive_config["triggers"][0]["timing"] == "action_before"
    assert skill.passive_config["triggers"][0]["effects"][0]["type"] == "heal_ratio"


def test_default_special_yaml_contains_guangming_top_enemy_templates() -> None:
    payload = Command()._load_heroes_payload("")

    purple_keys = {entry["key"] for entry in payload.get("purple", [])}
    boss_entry = next(entry for entry in payload.get("purple", []) if entry["key"] == "arena_gl_top_zhang_wuji_boss")
    wei_entry = next(entry for entry in payload.get("purple", []) if entry["key"] == "arena_gl_top_wei_yixiao_guard")
    front_entry = next(
        entry for entry in payload.get("purple", []) if entry["key"] == "arena_gl_top_five_flags_elite_front"
    )

    assert "arena_gl_top_zhang_wuji_boss" in purple_keys
    assert "arena_gl_top_yang_xiao_guard" in purple_keys
    assert "arena_gl_top_wei_yixiao_guard" in purple_keys
    assert "arena_gl_top_five_flags_elite_front" in purple_keys
    assert "arena_gl_top_five_flags_elite_rear" in purple_keys
    assert "gl_top_five_flags_barrier" in boss_entry["skills"]
    assert "gl_top_guard_morale" in wei_entry["skills"]
    assert "gl_top_guard_morale" in front_entry["skills"]


def test_default_arena_coop_special_skills_include_passive_configs() -> None:
    payload = Command()._load_skills_payload("")
    skills = {entry["key"]: entry for entry in payload.get("skills", [])}

    mingjiao = skills["gl_top_mingjiao_command"]
    qiankun = skills["gl_top_qiankun_shift"]
    holy_flame = skills["gl_top_holy_flame_rage"]
    five_flags = skills["gl_top_five_flags_barrier"]
    guard_morale = skills["gl_top_guard_morale"]

    assert mingjiao["passive_config"]["triggers"][0]["timing"] == "round_start"
    assert any(effect["type"] == "set_softcap" for effect in mingjiao["passive_config"]["triggers"][0]["effects"])
    assert any(effect["type"] == "emit_log" for effect in mingjiao["passive_config"]["triggers"][0]["effects"])
    assert any(effect["type"] == "emit_log" for effect in qiankun["passive_config"]["triggers"][0]["effects"])
    assert any(
        effect["type"] == "modify_outgoing_damage" and effect["value"] == 1.848
        for effect in holy_flame["passive_config"]["triggers"][0]["effects"]
    )
    assert any(effect["type"] == "set_softcap" for effect in holy_flame["passive_config"]["triggers"][0]["effects"])
    assert any(effect["type"] == "emit_log" for effect in holy_flame["passive_config"]["triggers"][0]["effects"])
    assert any(
        effect["type"] == "modify_incoming_damage" for effect in five_flags["passive_config"]["triggers"][0]["effects"]
    )
    assert any(effect["type"] == "emit_log" for effect in five_flags["passive_config"]["triggers"][0]["effects"])
    assert any(
        effect["type"] == "modify_outgoing_damage"
        for effect in guard_morale["passive_config"]["triggers"][0]["effects"]
    )
    assert any(effect["type"] == "emit_log" for effect in guard_morale["passive_config"]["triggers"][0]["effects"])


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

    wulin = GuestTemplate.objects.get(key="guild_wulinzhai_zhai_zhu")
    assert wulin.name == "乌鳞寨寨主"
    assert wulin.rarity == "purple"
    assert wulin.recruitable is False

    bloodflag = GuestTemplate.objects.get(key="guild_bloodflag_head_escort")
    assert bloodflag.name == "血旗总镖头"
    assert bloodflag.rarity == "purple"
    assert bloodflag.recruitable is False

    edward_blue = GuestTemplate.objects.get(key="orig_edward_blue")
    assert edward_blue.name == "爱德华"
    assert edward_blue.rarity == "blue"
    assert edward_blue.archetype == "military"
    assert edward_blue.recruitable is False

    edward_purple = GuestTemplate.objects.get(key="orig_edward_purple")
    assert edward_purple.name == "爱德华"
    assert edward_purple.rarity == "purple"
    assert edward_purple.archetype == "military"
    assert edward_purple.recruitable is False

    blackwind = GuestTemplate.objects.get(key="guild_blackwind_gate_general")
    assert blackwind.name == "黑风关守将"
    assert blackwind.rarity == "orange"
    assert blackwind.recruitable is False

    template = GuestTemplate.objects.get(key="task_barbarian_chanyu")
    assert template.name == "单于"
    assert template.rarity == "orange"
    assert template.recruitable is False
