from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from io import StringIO
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command

from guests.models import GuestTemplate
from guests.services.recruitment_templates import _get_recruitable_templates_by_rarity

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "reports" / "guest_expansion_ilrock_20261002.json"


@pytest.fixture(scope="module")
def expansion():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def roster():
    records = []
    for path in sorted((ROOT / "data" / "guests").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for rarity, entries in (payload.get("heroes") or {}).items():
            records.extend({**entry, "rarity": rarity, "file": path.name} for entry in entries)
    return records


@pytest.fixture(scope="module")
def identity_audit(expansion):
    return json.loads((ROOT / expansion["identity_audit_file"]).read_text(encoding="utf-8"))


def _identity(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def test_expansion_has_200_traceable_source_people(expansion, roster):
    selected = expansion["selected"]
    assert len(selected) == 200
    assert len({entry["source_id"] for entry in selected}) == 200
    assert len({entry["template_key"] for entry in selected}) == 200
    source = json.loads((ROOT / expansion["source_file"]).read_text(encoding="utf-8"))
    source_by_id = {entry["ID"]: entry for entry in source}
    catalog_by_key = {entry["key"]: entry for entry in roster}
    source_identity_owners = {}
    for entry in selected:
        original = source_by_id[entry["source_id"]]
        assert original["姓名"] == entry["source_name"]
        assert original["领域"] == entry["source_domain"]
        current = catalog_by_key[entry["template_key"]]
        assert (current["name"], current["rarity"], current["file"]) == (
            entry["name"],
            entry["rarity"],
            entry["roster_file"],
        )
        known_names = {entry["name"], entry["source_name"], *entry["aliases"]}
        for source_id in entry["source_alias_ids"]:
            assert source_by_id[source_id]["姓名"] in known_names
            previous = source_identity_owners.setdefault(source_id, entry["template_key"])
            assert previous == entry["template_key"]


def test_new_people_do_not_duplicate_existing_names_or_reviewed_aliases(expansion, roster):
    key_counts = Counter(entry["key"] for entry in roster)
    name_owners = {}
    for entry in roster:
        name_owners.setdefault(_identity(entry["name"]), set()).add(entry["key"])
    reviewed_identities = {}
    contextual_owners = {}
    for entry in expansion["contextual_aliases"]:
        contextual_owners.setdefault((entry["selected_template_key"], _identity(entry["alias"])), set()).add(
            entry["existing_different_person_key"]
        )
    for entry in expansion["selected"]:
        key = entry["template_key"]
        assert key_counts[key] == 1
        names = {entry["name"], entry["source_name"], *entry["aliases"]}
        for name in names:
            identity = _identity(name)
            allowed = {key} | contextual_owners.get((key, identity), set())
            assert name_owners.get(identity, set()) <= allowed, (entry["name"], name)
            previous = reviewed_identities.setdefault(identity, key)
            assert previous == key, (entry["name"], name)


def test_known_alias_and_typo_duplicates_are_excluded(expansion, roster):
    selected_ids = {entry["source_id"] for entry in expansion["selected"]}
    existing_keys = {entry["key"] for entry in roster}
    assert expansion["excluded_examples"]
    for entry in expansion["excluded_examples"]:
        assert entry["source_id"] not in selected_ids
        assert entry["existing_template_key"] in existing_keys
    selected_keys = {entry["template_key"] for entry in expansion["selected"]}
    for entry in expansion["contextual_aliases"]:
        assert entry["selected_template_key"] in selected_keys
        assert entry["existing_different_person_key"] in existing_keys
        assert entry["existing_different_person_key"] not in selected_keys
        assert entry["scope"] and entry["existing_source"] and entry["reference"]


def test_compound_titles_and_names_require_identity_review(expansion, roster):
    selected_keys = {entry["template_key"] for entry in expansion["selected"]}
    existing = [entry for entry in roster if entry["key"] not in selected_keys]
    reviewed = {
        (entry["selected_template_key"], entry["existing_template_key"])
        for entry in expansion["compound_name_reviews"]
        if entry["reason"]
    }
    overlaps = set()
    for entry in expansion["selected"]:
        names = {_identity(name) for name in {entry["name"], entry["source_name"], *entry["aliases"]}}
        for previous in existing:
            previous_name = _identity(previous["name"])
            if len(previous_name) < 2:
                continue
            if any(len(name) >= 2 and (name in previous_name or previous_name in name) for name in names):
                pair = (entry["template_key"], previous["key"])
                assert pair in reviewed, (entry["name"], previous["name"])
                overlaps.add(pair)
    assert overlaps == reviewed


def test_all_200_people_have_individual_identity_evidence(expansion, identity_audit, roster):
    selected = {entry["template_key"]: entry for entry in expansion["selected"]}
    reviewed = {entry["selected_template_key"]: entry for entry in identity_audit["entries"]}
    assert len(identity_audit["entries"]) == len(reviewed) == 200
    assert selected.keys() == reviewed.keys()
    existing_keys = {entry["key"] for entry in roster} - selected.keys()
    for key, entry in reviewed.items():
        assert entry["name"] == selected[key]["name"]
        assert {selected[key]["name"], selected[key]["source_name"], *selected[key]["aliases"]} <= set(
            entry["known_names"]
        )
        assert entry["verdict"] == "retain_distinct_identity"
        assert entry["references"]
        for match in entry["candidate_matches"]:
            assert match["existing_key"] in existing_keys
            assert match["verdict"] in {"different_identity", "distinct_person"}
            assert match["reason"]


def test_old_declared_aliases_are_checked_against_new_identity_names(expansion, identity_audit, roster):
    reviewed = {entry["selected_template_key"]: entry for entry in identity_audit["entries"]}
    selected_keys = reviewed.keys()
    declared_alias = re.compile(
        r"(?:本名|原名|初名|俗名|又名|曾名|法名|别名|更名|改名|化名|字|号称|别号|绰号|人称|外号|又称|通称|号)"
        r"(?:为|叫|是)?[“\"「『]?([\u4e00-\u9fff]{2,16})"
    )
    for previous in roster:
        if previous["key"] in selected_keys:
            continue
        aliases = {match.group(1) for match in declared_alias.finditer(previous.get("flavor", ""))}
        for entry in reviewed.values():
            if aliases & set(entry["known_names"]):
                matches = [match for match in entry["candidate_matches"] if match["existing_key"] == previous["key"]]
                assert matches, (entry["name"], previous["name"], aliases & set(entry["known_names"]))
                assert all(match["verdict"] in {"different_identity", "distinct_person"} for match in matches)


def test_new_people_follow_growth_and_archetype_rules(expansion, roster):
    catalog_by_key = {entry["key"]: entry for entry in roster}
    growth_limits = {"green": 9, "blue": 11, "purple": 14, "orange": 14}
    for selected in expansion["selected"]:
        entry = catalog_by_key[selected["template_key"]]
        assert entry.get("recruitable", True)
        assert entry["default_gender"] in {"male", "female"}
        assert len(entry["flavor"]) >= 70
        lower, upper = entry["growth_range"]
        assert 0 < lower <= upper <= growth_limits[entry["rarity"]]
        weights = entry["attribute_weights"]
        assert set(weights) == {"force", "intellect", "defense", "agility"}
        assert sum(weights.values()) == 100
        assert all(isinstance(value, int) and value > 0 for value in weights.values())
        if entry["archetype"] == "civil":
            assert weights["intellect"] > weights["force"]
        else:
            assert entry["archetype"] == "military"
            assert weights["force"] > weights["intellect"]


def test_new_people_have_plain_names_and_player_facing_biographies(expansion, roster):
    selected_keys = {entry["template_key"] for entry in expansion["selected"]}
    editorial_notes = (
        "不宜根据后世传说",
        "可靠记载有限",
        "资料不足",
        "源表",
        "本批",
        "模板编号",
        "稀有度",
        "未核实",
    )
    biographies = set()
    for entry in roster:
        if entry["key"] not in selected_keys:
            continue
        assert not re.search(r"[()（）\[\]【】]", entry["name"]), entry["name"]
        flavor = entry["flavor"]
        assert not any(note in flavor for note in editorial_notes), entry["name"]
        sentences = [sentence.strip() for sentence in re.split(r"[。！？]", flavor) if sentence.strip()]
        assert len(sentences) == len(set(sentences)), entry["name"]
        assert flavor not in biographies, entry["name"]
        biographies.add(flavor)


@pytest.mark.django_db
def test_default_import_makes_all_200_people_available_for_recruitment(expansion):
    call_command("load_guest_templates", skip_images=True, verbosity=0, stdout=StringIO())
    selected = {entry["template_key"]: entry for entry in expansion["selected"]}
    imported = GuestTemplate.objects.filter(key__in=selected)
    assert imported.count() == 200
    recruitable = _get_recruitable_templates_by_rarity()
    for template in imported:
        assert template.name == selected[template.key]["name"]
        assert template.rarity == selected[template.key]["rarity"]
        assert template.recruitable
        assert template.key in {entry.key for entry in recruitable[template.rarity]}
