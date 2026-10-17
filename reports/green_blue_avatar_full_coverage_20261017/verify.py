"""核对最新补图结果：原有头像、改动范围、图库覆盖和实际导入。"""

import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import django
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent
x = json.loads((REPORT / "manifest.json").read_text())
initial = json.loads((ROOT / "reports/green_blue_guest_avatars_20261017/manifest.json").read_text())
originals = {r["key"]: r for r in initial["records"]}
catalog = {
    r["avatar"]: r
    for r in json.loads((ROOT / "reports/green_blue_guest_avatar_traits_20261017/image_traits.json").read_text())
}
current = {}
for file in (ROOT / "data/guests").glob("*.yaml"):
    d = yaml.safe_load(file.read_text())
    for rarity in ("green", "blue"):
        for guest in d.get("heroes", {}).get(rarity, []):
            current[guest["key"]] = guest
assert len(current) == 3130
with zipfile.ZipFile(ROOT / "reports/green_blue_guest_avatars_20261017/original_rosters.zip") as archive:
    assert archive.testzip() is None
    for name in archive.namelist():
        before = yaml.safe_load(archive.read(name))
        after = yaml.safe_load((ROOT / name).read_text())
        for data in (before, after):
            for rarity in ("green", "blue"):
                for g in data.get("heroes", {}).get(rarity, []):
                    g.pop("avatar", None)
        assert before == after, name
preserved = 0
usage = Counter()
for r in x["records"]:
    guest = current[r["key"]]
    assert guest["avatar"] == r["avatar"]
    assert (ROOT / "data/images/guests" / r["avatar"]).is_file()
    if originals[r["key"]]["old_avatar"]:
        assert guest["avatar"] == originals[r["key"]]["old_avatar"]
        preserved += 1
    else:
        assert r["avatar"] in catalog
        assert catalog[r["avatar"]]["gender"] == r["profile"]["appearance_gender"]
        usage[r["avatar"]] += 1
assert preserved == 23 and set(usage) == set(catalog) and len(usage) == 177
changed = {r["key"] for r in x["records"] if "coverage_reason" in r}
assert len(changed) == 35
with zipfile.ZipFile(REPORT / "before_coverage.zip") as archive:
    assert archive.testzip() is None
    differences = set()
    for file in archive.namelist():
        before = yaml.safe_load(archive.read(file))
        after = yaml.safe_load((ROOT / file).read_text())
        for rarity in ("green", "blue"):
            for a, b in zip(before.get("heroes", {}).get(rarity, []), after.get("heroes", {}).get(rarity, [])):
                if a.get("avatar") != b.get("avatar"):
                    differences.add(a["key"])
    assert differences == changed
base = json.loads((ROOT / "reports/green_blue_guest_avatar_traits_20261017/manifest.json").read_text())
for r in base["records"]:
    if r["key"] not in changed:
        assert current[r["key"]]["avatar"] == r["avatar"]
duck = {p: usage[p] for p in catalog if p.startswith("duck_")}
assert len(duck) == 5 and min(duck.values()) > 0
assert {r["name"] for r in x["records"] if r["avatar"] == "duck_wu_male2.png"} == {"侯景", "李傕", "董卓"}
assert {r["name"] for r in x["records"] if r["avatar"] == "duck_wu_male1.png"} == {"丁春秋", "哈哈儿", "白开心", "霍都"}
django.setup()
from guests.management.commands.load_guest_templates import Command  # noqa: E402

command = Command(stdout=io.StringIO())
main = command._load_main_payload(ROOT / "data/guest_templates.yaml")
heroes = command._load_heroes_payload("")
sections = command._build_sections(main, {}, heroes)
command._append_hero_roster_templates(
    sections["template_data"], sections["hero_roster"], sections["attribute_profiles"], verbosity=0
)
loaded = {r["key"]: r for r in sections["template_data"]}
for r in x["records"]:
    assert loaded[r["key"]]["avatar"] == r["avatar"]
print(
    "Verified: 177/177 generic images and 5/5 duck images used; 35 targeted changes; 23 original avatars preserved; 3130 import mappings valid; all other fields and allocations unchanged."
)
