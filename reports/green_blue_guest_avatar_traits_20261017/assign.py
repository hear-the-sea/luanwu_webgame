"""绿蓝门客按生平、身份、兵器匹配通用头像；语义匹配优先于均摊。"""

import copy
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/images/guests"
INITIAL_RECORDS = json.loads((ROOT / "reports/green_blue_guest_avatars_20261017/manifest.json").read_text())["records"]
PRESET = {r["key"]: r["old_avatar"] for r in INITIAL_RECORDS if r["old_avatar"]}
NONHUMANS = {
    "task_jingyang_tiger": "menghu.png",
    "task_matchmaking_bear": "equip_jiguanxiong.png",
    "task_mechanism_cat": "equip_jixiemao.png",
    "myth_moon_jade_rabbit": "yutu.png",
}
# 优先识别身份和职业；兵器等作为进一步匹配依据。书名先移除，避免把所有射雕人物识别为射手。
RULES = {
    "monk": (100, r"僧人|高僧|禅师|方丈|和尚|僧侣|律宗|禅宗|佛学家|出家为僧|少林.{0,5}(?:僧|弟子|住持)|佛门"),
    "taoist": (95, r"道士|道长|道姑|道教|道门|全真|天师|道术|修道|炼丹|武当派|道家"),
    "healer": (98, r"神医|名医|医家|医圣|药王|医生|医者|医师|医学|医术|针灸|本草|伤寒|用毒与解毒|毒医|用毒高手|毒药"),
    "ruler": (
        96,
        r"开国皇帝|末代皇帝|第[一二三四五六七八九十百零0-9]{1,5}位?皇帝|[汉唐宋元明清隋魏晋齐梁陈周楚吴蜀]朝皇帝|国君|国主|登基|即位|称帝|在位",
    ),
    "noblewoman": (65, r"皇后|皇太后|皇妃|贵妃|公主|王妃|妃嫔|女皇|千金|闺秀"),
    "artist": (84, r"画家|书画家|书法家|书法|画师|画圣|绘画|善书|楷书|草书|行书|篆书|画论|书画"),
    "dancer": (87, r"舞女|舞伎|舞蹈|善舞|歌妓|歌伎|歌姬|歌女"),
    "musician": (
        88,
        r"音乐家|琴师|琴家|琴艺|琴曲|琴学|音律|琴箫|琴箫和鸣|弹琴|抚琴|琵琶|吹箫|善琴|歌唱|歌声|练歌|歌女",
    ),
    "astronomer": (87, r"天文学家|天文|历法|历算|星象|浑天仪|数学家|算学|数学|算经"),
    "sailor": (83, r"航海家|下西洋|远航|航海|使节西行|远洋|海商|海上贸易|探险家"),
    "merchant": (82, r"商人|商贾|富商|经商|理财|财政|会计|钱庄|财税|商号|账房|盐铁|赋税|财赋"),
    "beggar": (86, r"丐帮|乞丐|叫花|丐侠|丐王"),
    "constable": (81, r"捕快|捕头|名捕|神捕|法医|断案|刑名|狱讼|执法|捕神"),
    "assassin": (79, r"刺客|杀手|暗器|飞刀|盗帅|侠盗|大盗|盗贼"),
    "archer": (78, r"神射|善射|箭术|射艺|弓术|射手|百步穿杨|百发百中|射雕手|连珠箭"),
    "hammer": (80, r"铜锤|铁锤|双锤|大锤|锤法|流星锤"),
    "axeman": (80, r"巨斧|大斧|板斧|斧法|开山斧|宣花斧"),
    "bladesman": (77, r"刀法|刀客|刀术|双刀|大刀|偃月刀|宝刀|快刀|弯刀|刀王"),
    "spearman": (77, r"枪法|枪术|银枪|长枪|铁枪|钢枪|双枪|大枪|枪王"),
    "swordsman": (76, r"剑客|剑法|剑术|剑道|剑招|剑仙|剑侠|剑神|剑圣|剑宗|剑气|御剑|剑士|使剑"),
    "boxer": (73, r"拳法|拳术|拳脚|掌法|掌力|拳掌|铁掌|空手|徒手|八卦掌|太极拳"),
    "general": (64, r"将领|武将|名将|大将|将军|军事家|都督|统帅|统兵|领兵|元帅|节度使|边将|女英雄|代父从军"),
    "strategist": (69, r"军师|谋士|谋略家|谋主|智囊|运筹|参谋|筹谋|策士|纵横家|外交家|兵法家"),
    "official": (50, r"大臣|官员|宰相|权臣|丞相|尚书|侍郎|御史|知府|太守|政治家|刺史|阁老|首辅|官至"),
    "scholar": (54, r"学者|思想家|经学家|儒学家|哲学家|诗人|文学家|史学家|史家|博学|著述|学士|诗文|词人|武学典籍"),
    "hermit": (65, r"隐士|隐居|隐逸|归隐|山林|遁世|隐者|高士|闲云野鹤"),
    "bandit": (62, r"土匪|山匪|寨主|匪首|强盗|山贼|盗匪"),
}
# 从当前配置人物生平核查的特例：同一名字不同模板保持同一人物特点。
NAME_HINTS = {
    "程灵素": {"healer": 150},
    "胡青牛": {"healer": 150},
    "薛慕华": {"healer": 150},
    "平一指": {"healer": 150},
    "蓝凤凰": {"healer": 130, "noblewoman": 45},
    "一行": {"monk": 130, "astronomer": 90},
    "怀素": {"monk": 130, "artist": 90},
    "鉴真": {"monk": 140},
    "丘处机": {"taoist": 140},
    "公孙胜": {"taoist": 140},
    "王语嫣": {"scholar": 125, "noblewoman": 80},
    "成不忧": {"swordsman": 140},
    "白自在": {"swordsman": 130, "elder": 50},
    "花木兰": {"general": 135, "heroine": 120},
    "蔡京": {"official": 145},
    "完颜陈和尚": {"general": 160},
    "移剌蒲阿": {"general": 160},
    "楚昭南": {"swordsman": 150},
    "耶律倍": {"ruler": 150, "artist": 80},
    "缇萦": {"scholar": 140},
    "赵广汉": {"constable": 140},
    "明月心": {"strategist": 150},
    "康敏": {"noblewoman": 145},
    "凌霜华": {"noblewoman": 145},
    "田伯光": {"bladesman": 140},
    "李应": {"spearman": 140},
    "吴刚": {"axeman": 150},
    "蒲巴乙": {"singer": 150, "young": 50},
    "爱德华": {"alchemist": 140},
}
TRANSLATE = {
    "monk": "僧人／佛门",
    "taoist": "道士／道术",
    "healer": "医药／解毒",
    "ruler": "君王",
    "noblewoman": "闺秀／宫廷",
    "artist": "书画",
    "musician": "琴箫音律",
    "astronomer": "天文历算",
    "sailor": "远行航海",
    "merchant": "理财商贸",
    "beggar": "丐帮／乞者",
    "constable": "捕快／刑名",
    "assassin": "暗器／刺客",
    "archer": "弓箭",
    "hammer": "锤",
    "axeman": "斧",
    "bladesman": "刀",
    "spearman": "枪",
    "swordsman": "剑",
    "boxer": "拳掌",
    "general": "将领／军旅",
    "strategist": "谋士",
    "official": "官员",
    "scholar": "文士／书卷",
    "hermit": "隐逸",
    "bandit": "山寨／匪徒",
    "hero": "江湖武人",
    "heroine": "女侠",
    "singer": "歌唱",
    "alchemist": "炼金",
    "dancer": "歌舞",
}
WEAPONS = {"archer", "hammer", "axeman", "bladesman", "spearman", "swordsman"}
SPECIAL = {
    "monk",
    "taoist",
    "healer",
    "ruler",
    "beggar",
    "musician",
    "astronomer",
    "sailor",
    "merchant",
    "singer",
    "alchemist",
}


def stable(key):
    return hashlib.sha256(("guest-traits-v2:" + key).encode()).hexdigest()


def profile(guest):
    flavor = guest.get("flavor", "")
    text = re.sub(r"《[^》]*》", "", flavor)
    text = re.sub(r"出自.*$", "", text)
    text = re.sub(r"（[^）]*）", "", text).strip()
    identity = re.split(r"[。；]", text, maxsplit=1)[0][:180]
    features = {}
    matches = {}
    for tag, (weight, pattern) in RULES.items():
        found = list(re.finditer(pattern, text))
        if not found or tag == "ruler":
            continue
        in_identity = any(m.start() < len(identity) for m in found)
        # 身份类只由开头的自我介绍确定；后文提及他人或死亡原因不改职业。
        if in_identity:
            value = weight + 15 + min(4, len(found) - 1)
        elif tag in WEAPONS | {"boxer"}:
            value = weight
        elif tag in {"artist", "musician", "astronomer", "healer"}:
            value = weight - 30
        else:
            value = 25
        features[tag] = value
        matches[tag] = sorted({m.group(0) for m in found})[:6]
    # “某帝即位后赐死他”不表示此人是皇帝；优先看首个身份短语里的本人的头衔。
    first_title = re.split(r"[，、]", identity, maxsplit=1)[0].replace(guest["name"], "").strip(" ，。")
    own_title = bool(
        re.search(r"(?:[帝王]$|[太高世][祖宗]$|[宣肃中仁英神穆宪敬懿僖孝睿宁理度光真哲钦徽端成武文][宗]$)", first_title)
    )
    own_title = own_title or bool(
        re.search(
            r"^(?:.{0,8})?(?:开国皇帝|末代皇帝|第[一二三四五六七八九十百零0-9]{1,5}位?皇帝|国君|国主|君主)",
            identity[:45],
        )
    )
    if own_title:
        features["ruler"] = 120
        matches["ruler"] = [first_title]
    gender = guest.get("default_gender", "unknown")
    # 生平明确记载为女商人，头像采用女性；不改动原有游戏性别字段。
    if guest["name"] == "巴寡妇清" and "女商人" in text:
        gender = "female"
    if gender == "male":
        features.pop("noblewoman", None)
    if re.search(r"皇后|皇太后|皇妃|贵妃|公主|王妃|妃嫔|女皇|太后|嫔妃|宠妃", identity):
        features["royal"] = 60
    if re.search(r"白须|白髯|鹤发|老僧|老道|老者|耆宿|老妪|老将|白发", text):
        features["elder"] = 35
    if re.search(r"少年|少女|年轻|青年|幼女", text[:150]):
        features["young"] = 30
    if guest["archetype"] == "military":
        features.setdefault("heroine" if gender == "female" else "hero", 40)
        if guest["key"].startswith("hist_"):
            features.setdefault("general", 55)
    else:
        features.setdefault("noblewoman" if gender == "female" else "scholar", 40)
    features.update(NAME_HINTS.get(guest["name"], {}))
    primary = max((t for t in features if t not in {"young", "elder", "royal"}), key=lambda t: (features[t], t))
    return {
        "features": features,
        "matches": matches,
        "primary": primary,
        "modern_allowed": guest["name"] == "蒲巴乙",
        "source": text,
        "identity": identity,
        "appearance_gender": gender,
    }


def score_image(p, image, guest):
    tags = set(image["traits"])
    f = p["features"]
    if image["gender"] != p["appearance_gender"]:
        return None
    if image["era"] == "modern" and not p["modern_allowed"]:
        return None
    # 未提及的强身份道具不作为普通文士或武将的兜底形象。
    allowed_special = set(f) | ({"taoist"} if p["primary"] == "alchemist" else set())
    if (tags & SPECIAL) - allowed_special:
        return None
    primary = p["primary"]
    if tags & {"crown", "empress"} and "ruler" not in f and "royal" not in f:
        return None
    score = 0
    if primary in tags:
        score += 200
    elif primary == "heroine" and "heroine" in tags:
        score += 200
    elif primary in WEAPONS and p["appearance_gender"] == "female" and tags & (WEAPONS | {"daggers"}):
        score += 180
    elif primary == "general" and p["appearance_gender"] == "female" and "heroine" in tags:
        score += 175 + (15 if tags & (WEAPONS | {"daggers"}) else 0)
    elif primary == "ruler" and p["appearance_gender"] == "female" and "noblewoman" in tags:
        score += 170 + (20 if tags & {"crown", "empress"} else 0)
    elif (
        primary in {"artist", "strategist", "merchant", "astronomer"}
        and p["appearance_gender"] == "female"
        and "book" in tags
    ):
        score += 170
    elif (
        primary in {"taoist", "monk", "healer", "hermit"}
        and p["appearance_gender"] == "female"
        and "noblewoman" in tags
    ):
        score += 160  # 缺乏对应女性素材时保留古装气质，审计中明确标出近似匹配。
    elif primary == "singer" and "singer" in tags:
        score += 200
    else:
        score += 0
    # 首要特点决定候选层级，次要特点作小幅加分，不能压过主身份。
    for tag, weight in f.items():
        if tag in tags:
            score += min(14, weight / 10)
    if image["archetype"] == guest["archetype"]:
        score += 8
    if primary not in {"general", "hero", "heroine"} and "armor" in tags and "general" not in f:
        score -= 70
    if "ruler" in tags and primary != "ruler":
        score -= 80
    if "monk" in tags and primary != "monk":
        score -= 80
    if "taoist" in tags and primary != "taoist":
        score -= 50
    if "young" in f and "elder" in tags:
        score -= 60
    if "elder" in f and "young" in tags:
        score -= 60
    if primary == "scholar" and "book" in tags:
        score += 6
    if primary == "artist" and tags & {"painter", "calligrapher"}:
        score += 6
    if primary == "healer" and "herbal" in tags:
        score += 4
    if primary == "strategist" and "fan" in tags:
        score += 5
    if "royal" in f and tags & {"crown", "empress"}:
        score += 25
    if "royal" not in f and "ruler" not in f and tags & {"crown", "empress"}:
        score -= 80
    if primary == "singer" and "singer" in tags:
        score += 20
    return score


def main():
    catalog = json.loads((REPORT / "image_traits.json").read_text())
    prior_path = REPORT / "manifest.json"
    prior_records = {r["key"]: r for r in json.loads(prior_path.read_text())["records"]} if prior_path.exists() else {}
    originals = {p: p.read_text() for p in sorted((ROOT / "data/guests").glob("*.yaml"))}
    data = {p: yaml.safe_load(s) for p, s in originals.items()}
    targets = []
    for path, d in data.items():
        for rarity in ("green", "blue"):
            for g in d.get("heroes", {}).get(rarity, []):
                targets.append({"file": path, "rarity": rarity, "guest": g})
    assert len(targets) == 3130
    bundles = defaultdict(list)
    for target in targets:
        g = target["guest"]
        if g["key"] in PRESET:
            continue
        p = profile(g)
        target["profile"] = p
        scored = [(score_image(p, i, g), i) for i in catalog]
        scored = [(s, i) for s, i in scored if s is not None]
        assert scored, g["key"]
        best = max(s for s, i in scored)
        # 仅在接近最佳匹配的候选中平衡数量；不为均摊选择身份不符的图片。
        choices = [(s, i) for s, i in scored if s >= best - 7]
        target["choices"] = choices
        bundles[(g["name"], p["appearance_gender"], p["primary"])].append(target)
    usage = Counter()
    allocations = {}
    details = {}
    ordered = sorted(bundles.values(), key=lambda b: (len(b[0]["choices"]), -len(b), stable(b[0]["guest"]["key"])))
    for bundle in ordered:
        common = set(i["avatar"] for s, i in bundle[0]["choices"])
        for t in bundle[1:]:
            common &= {i["avatar"] for s, i in t["choices"]}
        if common:
            choices = [(s, i) for s, i in bundle[0]["choices"] if i["avatar"] in common]
            selected_score, selected = min(
                choices,
                key=lambda pair: (
                    usage[pair[1]["avatar"]],
                    -pair[0],
                    stable(pair[1]["avatar"] + bundle[0]["guest"]["key"]),
                ),
            )
            for t in bundle:
                key = t["guest"]["key"]
                allocations[key] = selected["avatar"]
                details[key] = {
                    "profile": t["profile"],
                    "selected_traits": selected["traits"],
                    "score": selected_score,
                    "candidate_count": len(choices),
                    "primary_matched": t["profile"]["primary"] in selected["traits"],
                }
            usage[selected["avatar"]] += len(bundle)
        else:
            for t in bundle:
                selected_score, selected = min(
                    t["choices"],
                    key=lambda pair: (
                        usage[pair[1]["avatar"]],
                        -pair[0],
                        stable(pair[1]["avatar"] + t["guest"]["key"]),
                    ),
                )
                key = t["guest"]["key"]
                allocations[key] = selected["avatar"]
                details[key] = {
                    "profile": t["profile"],
                    "selected_traits": selected["traits"],
                    "score": selected_score,
                    "candidate_count": len(t["choices"]),
                    "primary_matched": t["profile"]["primary"] in selected["traits"],
                }
                usage[selected["avatar"]] += 1
    allocations.update(PRESET)
    records = []
    updates = {}
    for path, original in originals.items():
        before = data[path]
        after = copy.deepcopy(before)
        node = yaml.compose(original)
        hero_node = next((v for k, v in node.value if k.value == "heroes"), None)
        if hero_node is None:
            continue
        edits = []
        for rarity_node, rows_node in hero_node.value:
            rarity = rarity_node.value
            if rarity not in {"green", "blue"}:
                continue
            for g, g_node in zip(after["heroes"][rarity], rows_node.value):
                key = g["key"]
                avatar = allocations[key]
                assert (SOURCE / avatar).is_file()
                record = {
                    "key": key,
                    "name": g["name"],
                    "rarity": rarity,
                    "gender": g.get("default_gender"),
                    "archetype": g["archetype"],
                    "file": path.relative_to(ROOT).as_posix(),
                    "old_avatar": g.get("avatar"),
                    "avatar": avatar,
                    "nonhuman": key in NONHUMANS,
                    "changed": g.get("avatar") != avatar,
                    "preserved": key in PRESET,
                    "avatar_before_initial_assignment": PRESET.get(key),
                    **details.get(key, {}),
                }
                if key in prior_records:
                    record["old_avatar"] = prior_records[key]["old_avatar"]
                    record["changed"] = record["old_avatar"] != avatar
                records.append(record)
                if g.get("avatar") == avatar:
                    continue
                fields = {k.value: v for k, v in g_node.value}
                assert "avatar" in fields
                av = fields["avatar"]
                edits.append((av.start_mark.index, av.end_mark.index, avatar))
                g["avatar"] = avatar
        output = original
        for start, end, replacement in sorted(edits, reverse=True):
            output = output[:start] + replacement + output[end:]
        assert yaml.safe_load(output) == after
        for d in (before, after):
            for rarity in ("green", "blue"):
                for g in d.get("heroes", {}).get(rarity, []):
                    g.pop("avatar", None)
        assert before == after, path
        if edits:
            updates[path] = output
    backup = REPORT / "before_traits.zip"
    if not backup.exists():
        with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in updates:
                archive.write(path, path.relative_to(ROOT).as_posix())
    else:
        with zipfile.ZipFile(backup) as archive:
            for path in updates:
                rel = path.relative_to(ROOT).as_posix()
                if rel not in archive.namelist():
                    continue
                baseline = yaml.safe_load(archive.read(rel))
                current = yaml.safe_load(originals[path])
                for d in (baseline, current):
                    for rarity in ("green", "blue"):
                        for g in d.get("heroes", {}).get(rarity, []):
                            g.pop("avatar", None)
                assert baseline == current, path
    for path in updates:
        assert path.read_text() == originals[path]
    for path, output in updates.items():
        path.write_text(output)
    audit = {
        "total": len(records),
        "changed": sum(r["changed"] for r in records),
        "files": len({r["file"] for r in records if r["changed"]}),
        "preserved_count": len(PRESET),
        "new_assignments": len(records) - len(PRESET),
        "images_used": len(usage),
        "primary_matched": sum(bool(r.get("primary_matched")) for r in records),
        "primary_counts": dict(Counter(r["profile"]["primary"] for r in records if "profile" in r)),
        "image_usage": dict(usage),
        "records": records,
    }
    (REPORT / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in audit.items() if k not in {"records", "image_usage"}}, ensure_ascii=False, indent=2
        )
    )
    for name in (
        "平一指",
        "胡青牛",
        "程灵素",
        "鉴真",
        "丘处机",
        "公孙胜",
        "虞世南",
        "一行",
        "成不忧",
        "花木兰",
        "蔡京",
        "蒲巴乙",
        "爱德华",
    ):
        for r in records:
            if r["name"] == name:
                print(name, r.get("profile", {}).get("primary", "原图保留"), r["avatar"], r.get("selected_traits", []))


if __name__ == "__main__":
    main()
