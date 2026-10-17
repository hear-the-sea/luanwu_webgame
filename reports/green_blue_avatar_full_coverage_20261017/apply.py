"""按用户最新偏好补用全部通用素材，保留原有 23 个头像和其余已匹配角色。"""

import copy
import json
import zipfile
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent
BASELINE = ROOT / "reports/green_blue_guest_avatar_traits_20261017/manifest.json"
INITIAL = ROOT / "reports/green_blue_guest_avatars_20261017/manifest.json"
# 这些对应关系按现有配置生平人工核对。现代元素作为卡通表现使用，不宣称还原历史服饰。
PLANS = [
    (
        "duck_wen_male1.png",
        ["赵高", "李林甫", "严嵩", "贾似道"],
        "精明文人形象，用于配置中有矫诏、排斥政敌或专权事迹的权谋人物",
    ),
    (
        "duck_wen_male3.png",
        ["卢杞", "杨国忠", "丁谓"],
        "戴帽、眼镜的文人形象，用于争权或决策失当的文臣，采用卡通化表现",
    ),
    (
        "duck_wu_male1.png",
        ["丁春秋", "哈哈儿", "白开心", "霍都"],
        "带危险道具的夸张武系形象，用于生平明确记载阴险、恶行的江湖反派",
    ),
    ("duck_wu_male2.png", ["侯景", "李傕", "董卓"], "持武器的武人形象，用于发动兵变或挟持朝廷的负面武人"),
    ("monkey_wen_female6.png", ["明月心"], "手持书册的女性形象，匹配女军师的谋划特点"),
    ("monkey_wen_female1.png", ["屠娇娇"], "俏皮便装女性，匹配其精通易容、演戏、善变的特点；接受卡通化装束"),
    ("monkey_wen_female4.png", ["林仙儿"], "明艳女性形象，匹配以美色与谎言操纵他人的设定"),
    ("monkey_wen_female8.png", ["康敏"], "时髦女性形象，匹配其外表柔弱、内心阴狠的反差设定"),
    ("monkey_wu_female4.png", ["阿紫"], "活泼少女的外观，匹配其古灵精怪的年龄气质；不表示棒球是其技能"),
    ("monkey_wu_female3.png", ["梅若华"], "拳击动作的女性武人，近似表现其以爪法近身进攻的特点"),
    ("monkey_wu_female2.png", ["上官飞燕"], "明艳女性形象，表现其以美貌迷惑他人的设定；采用卡通化装束"),
    ("pig/文046.png", ["尹克西"], "洋帽商人形象，匹配波斯大贾、贩卖珠宝的身份"),
    ("pig/文043.png", ["李少君"], "手持炉器的方士，匹配祠灶、炼丹求仙的身份"),
    ("pig/文045.png", ["张横"], "手持船只模型，匹配船家、水军头领的身份"),
    ("pig_wu_male6.png", ["乌鳞夜哨"], "戴帽的普通武人，匹配山寨守夜哨兵"),
    ("pig/武087.png", ["公冶乾"], "徒手武人，匹配其擅长掌法的特点"),
    ("pig/武085.png", ["陈玄风"], "徒手武人，匹配横练硬功、近身搏斗的特点"),
    ("pig/武086.png", ["鹿杖客"], "持棍杖武人，匹配其名号和玄冥武人的身份"),
    ("pig/武030.png", ["阿二"], "徒手武人，匹配天生神力、外家硬功"),
    ("monkey_wu_male9.png", ["魏良辅"], "歌唱形象，匹配改造昆山腔、传授唱腔曲艺的音乐家身份"),
    ("pig/武089.png", ["金九龄"], "持表、戴帽的侦探形象，匹配神捕与风流讲究的身份"),
    ("pig/武068.png", ["余沧海"], "短刃武人，保留其武装、阴狠的江湖反派形象；不还原青城派服饰"),
    ("monkey_wu_male6.png", ["张绣"], "盔甲与长兵器，匹配领军武将的身份"),
    ("pig/武073.png", ["杜夔"], "手持乐器，匹配太乐令、校正乐器和教授乐工的身份"),
    ("pig/武074.png", ["阿三"], "徒手武人，匹配大力金刚指的近身搏斗特点"),
]


def main():
    base = json.loads(BASELINE.read_text())
    initial = json.loads(INITIAL.read_text())
    preserved = {r["key"]: r["old_avatar"] for r in initial["records"] if r["old_avatar"]}
    images = {r["avatar"]: r for r in json.loads((BASELINE.parent / "image_traits.json").read_text())}
    by_name = {}
    for r in base["records"]:
        by_name.setdefault(r["name"], []).append(r)
    counts = Counter(r["avatar"] for r in base["records"] if r["avatar"] in images)
    selections = {}
    for avatar, names, reason in PLANS:
        assert avatar in images and (ROOT / "data/images/guests" / avatar).is_file(), avatar
        for name in names:
            choices = [r for r in by_name.get(name, []) if r["key"] not in preserved]
            assert len(choices) == 1, (name, len(choices))
            record = choices[0]
            gender = record["profile"]["appearance_gender"]
            assert images[avatar]["gender"] == gender, name
            assert record["key"] not in selections, name
            selections[record["key"]] = {
                "name": name,
                "avatar": avatar,
                "reason": reason,
                "before_avatar": record["avatar"],
                "file": record["file"],
            }
            counts[record["avatar"]] -= 1
            counts[avatar] += 1
    # 不允许补用新图的同时，使原本在用的其他通用图无人使用。
    missing = [image for image in images if counts[image] < 1]
    assert not missing, missing
    source_files = sorted({r["file"] for r in selections.values()})
    originals = {name: (ROOT / name).read_text() for name in source_files}
    outputs = {}
    for name, text in originals.items():
        before = yaml.safe_load(text)
        after = copy.deepcopy(before)
        node = yaml.compose(text)
        heroes = next(v for k, v in node.value if k.value == "heroes")
        edits = []
        for rarity_node, group in heroes.value:
            rarity = rarity_node.value
            if rarity not in {"green", "blue"}:
                continue
            for guest, g_node in zip(after["heroes"][rarity], group.value):
                if guest["key"] not in selections:
                    continue
                selected = selections[guest["key"]]
                assert guest["avatar"] == selected["before_avatar"], (name, guest["key"], "出现并发头像修改")
                fields = {k.value: v for k, v in g_node.value}
                av = fields["avatar"]
                edits.append((av.start_mark.index, av.end_mark.index, selected["avatar"]))
                guest["avatar"] = selected["avatar"]
        result = text
        for start, end, replacement in sorted(edits, reverse=True):
            result = result[:start] + replacement + result[end:]
        assert yaml.safe_load(result) == after
        for d in (before, after):
            for rarity in ("green", "blue"):
                for guest in d.get("heroes", {}).get(rarity, []):
                    guest.pop("avatar", None)
        assert before == after
        outputs[name] = result
    backup = REPORT / "before_coverage.zip"
    with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_files:
            archive.write(ROOT / name, name)
    for name, text in originals.items():
        assert (ROOT / name).read_text() == text
    for name, result in outputs.items():
        (ROOT / name).write_text(result)
    records = copy.deepcopy(base["records"])
    for record in records:
        if record["key"] in selections:
            selection = selections[record["key"]]
            record["before_coverage_avatar"] = record["avatar"]
            record["avatar"] = selection["avatar"]
            record["coverage_reason"] = selection["reason"]
            record["score"] = None
            record["candidate_count"] = None
            record["match_policy"] = "user_full_coverage"
            record["changed"] = record.get("old_avatar") != record["avatar"]
            record["selected_traits"] = images[record["avatar"]]["traits"]
            record["primary_matched"] = record["profile"]["primary"] in record["selected_traits"]
    used = Counter(r["avatar"] for r in records if not r["preserved"])
    assert set(used) == set(images)
    audit = {
        "total": len(records),
        "preserved_count": len(preserved),
        "changed_this_pass": len(selections),
        "files_this_pass": len(outputs),
        "generic_images": len(images),
        "generic_images_used": len(used),
        "duck_images_used": {i: used[i] for i in images if i.startswith("duck_")},
        "coverage_assignments": list(selections.values()),
        "image_usage": dict(used),
        "records": records,
    }
    (REPORT / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in audit.items() if k not in {"records", "image_usage", "coverage_assignments"}},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
