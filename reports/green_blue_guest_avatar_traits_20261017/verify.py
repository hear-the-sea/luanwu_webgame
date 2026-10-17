"""独立核对：原有头像保留、属性不变、匹配范围及关键人物回归。"""

import io
import json
import zipfile
from pathlib import Path

import django
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent
x = json.loads((REPORT / "manifest.json").read_text())
initial = json.loads((ROOT / "reports/green_blue_guest_avatars_20261017/manifest.json").read_text())
originals = {r["key"]: r for r in initial["records"]}
catalog = {r["avatar"]: r for r in json.loads((REPORT / "image_traits.json").read_text())}
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
        for d in (before, after):
            for rarity in ("green", "blue"):
                for guest in d.get("heroes", {}).get(rarity, []):
                    guest.pop("avatar", None)
        assert before == after, name
preserved = 0
new = 0
for r in x["records"]:
    key = r["key"]
    guest = current[key]
    assert guest["avatar"] == r["avatar"]
    assert (ROOT / "data/images/guests" / r["avatar"]).is_file()
    if originals[key]["old_avatar"]:
        assert r["preserved"] and guest["avatar"] == originals[key]["old_avatar"], key
        preserved += 1
    else:
        assert not r["preserved"] and r["avatar"] in catalog
        image = catalog[r["avatar"]]
        assert image["gender"] == r["profile"]["appearance_gender"]
        if guest["name"] != "巴寡妇清":
            assert image["gender"] == guest["default_gender"]
        else:
            assert image["gender"] == "female" and "女商人" in guest["flavor"]
        assert image["era"] == "traditional"
        new += 1
assert (preserved, new) == (23, 3107)
by_name = {r["name"]: r for r in x["records"]}
expect = {
    "平一指": "healer",
    "胡青牛": "healer",
    "程灵素": "healer",
    "薛慕华": "healer",
    "鉴真": "monk",
    "一行": "monk",
    "丘处机": "taoist",
    "公孙胜": "taoist",
    "虞世南": "artist",
    "成不忧": "swordsman",
    "楚昭南": "swordsman",
    "蔡京": "official",
    "和珅": "official",
    "方孝孺": "scholar",
    "董仲舒": "scholar",
    "胡大海": "general",
    "荀攸": "strategist",
    "黄霸": "official",
    "完颜陈和尚": "general",
    "移剌蒲阿": "general",
    "赵广汉": "constable",
    "王语嫣": "scholar",
    "王伯当": "archer",
    "花荣": "archer",
    "李应": "spearman",
    "耶律倍": "ruler",
    "田伯光": "bladesman",
}
for name, role in expect.items():
    r = by_name[name]
    assert r["profile"]["primary"] == role, (name, r["profile"]["primary"], role)
    assert role in r["selected_traits"], (name, r["avatar"], r["selected_traits"])
for name in ("青青", "第二梦", "秦红棉", "扈三娘"):
    assert set(by_name[name]["selected_traits"]) & {"daggers", "swordsman", "bladesman"}, name
assert by_name["程灵素"]["avatar"] == "monkey/其他随机角色/文059_女.png"
assert by_name["虞世南"]["avatar"] == "monkey/其他随机角色/文_男.png"
assert by_name["吴三桂"]["avatar"] == "wusangui.png"
assert by_name["玉兔"]["avatar"] == "yutu.png"
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
    f"Verified: {preserved} original avatars preserved, {new} new assignments, {len(expect)} identity cases plus 4 female weapon cases, 3130 import mappings; other attributes and rarities unchanged."
)
