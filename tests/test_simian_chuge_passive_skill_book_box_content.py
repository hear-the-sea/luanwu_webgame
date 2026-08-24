from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_yaml(filename: str) -> dict:
    return yaml.safe_load((DATA_DIR / filename).read_text(encoding="utf-8"))


def _current_passive_skill_book_keys(items: dict[str, dict]) -> set[str]:
    skills: dict[str, dict] = {}
    for filename in ("guest_skills.yaml", "arena_coop_special_skills.yaml"):
        skills.update({row["key"]: row for row in _load_yaml(filename)["skills"]})

    passive_skill_keys = {key for key, skill in skills.items() if skill.get("kind") == "passive"}
    return {
        key
        for key, item in items.items()
        if item.get("effect_type") == "skill_book"
        and (item.get("effect_payload") or {}).get("skill_key") in passive_skill_keys
    }


def test_simian_chuge_drops_passive_skill_book_box_at_configured_rate():
    missions = {row["key"]: row for row in _load_yaml("mission_templates.yaml")["missions"]}

    assert missions["simian_chuge"]["drop_table"]["passive_skill_book_box"] == 0.05


def test_passive_skill_book_box_contains_every_current_passive_skill_book():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}
    box = items["passive_skill_book_box"]
    choices = box["effect_payload"]["skill_book_choices"]
    choice_keys = {choice["item_key"] for choice in choices}

    assert box["effect_type"] == "loot_box"
    assert box["effect_payload"]["skill_book_chance"] == 1
    assert choice_keys == _current_passive_skill_book_keys(items)
    assert len(choice_keys) == 7
    assert {choice["weight"] for choice in choices} == {1}
    assert all(items[key]["effect_type"] == "skill_book" for key in choice_keys)


def test_task_skill_book_boxes_reuse_large_work_chest_image():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}

    assert items["civil_skill_book_box"]["image"] == "large.png"
    assert items["passive_skill_book_box"]["image"] == "large.png"
    assert (DATA_DIR / "images" / "items" / "large.png").is_file()
