"""为绿蓝门客分配文武、性别匹配的通用头像，组内严格均衡。"""

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
SOURCES = ROOT / "data/images/guests"
ROSTER = ROOT / "data/guests"
RARITIES = {"green", "blue"}
NONHUMANS = {
    "task_jingyang_tiger": "menghu.png",
    "task_matchmaking_bear": "equip_jiguanxiong.png",
    "task_mechanism_cat": "equip_jixiemao.png",
}


def stable_order(value):
    return hashlib.sha256(("green-blue-avatar-v1:" + value).encode()).hexdigest()


def build_catalog():
    catalog = defaultdict(list)
    for path in sorted(SOURCES.iterdir()):
        match = re.fullmatch(r"(monkey|pig|duck)_(wen|wu)_(male|female)\d*\.png", path.name)
        if match:
            role = "civil" if match[2] == "wen" else "military"
            catalog[(role, match[3])].append(path.relative_to(SOURCES).as_posix())
    for path in sorted((SOURCES / "monkey/其他随机角色").glob("*.png")):
        assert path.stem[0] in "文武" and ("男" in path.stem or "女" in path.stem), path
        role = "civil" if path.stem[0] == "文" else "military"
        gender = "female" if "女" in path.stem else "male"
        catalog[(role, gender)].append(path.relative_to(SOURCES).as_posix())
    # 猪系编号图片已逐页检查，均为男性形象；文／武由文件名前缀确定。
    for path in sorted((SOURCES / "pig").glob("*.png")):
        assert path.stem[0] in "文武", path
        role = "civil" if path.stem[0] == "文" else "military"
        catalog[(role, "male")].append(path.relative_to(SOURCES).as_posix())
    for key, images in catalog.items():
        assert len(images) == len(set(images)), key
        images.sort(key=stable_order)
    assert sum(map(len, catalog.values())) == 177
    return dict(catalog)


def main():
    catalog = build_catalog()
    parsed = {}
    originals = {}
    targets = []
    for path in sorted(ROSTER.glob("*.yaml")):
        text = path.read_text()
        data = yaml.safe_load(text)
        originals[path] = text
        parsed[path] = data
        for rarity in RARITIES:
            for guest in data.get("heroes", {}).get(rarity, []):
                targets.append(
                    {
                        "path": path,
                        "rarity": rarity,
                        "guest": guest,
                        "group": (guest["archetype"], guest.get("default_gender", "unknown")),
                    }
                )
    assert len(targets) == 3130, len(targets)
    assert len({t["guest"]["key"] for t in targets}) == len(targets)
    grouped = defaultdict(list)
    for target in targets:
        if target["guest"]["key"] not in NONHUMANS:
            assert target["group"] in catalog, target
            grouped[target["group"]].append(target)
    allocations = dict(NONHUMANS)
    group_stats = []
    for group, guests in sorted(grouped.items()):
        images = catalog[group]
        # 固定排序后循环分配，结果可复现；不依赖当前配置，重复运行不会换脸。
        for index, target in enumerate(sorted(guests, key=lambda t: stable_order(t["guest"]["key"]))):
            allocations[target["guest"]["key"]] = images[index % len(images)]
        usage = Counter(allocations[t["guest"]["key"]] for t in guests)
        assert len(usage) == len(images) and max(usage.values()) - min(usage.values()) <= 1
        group_stats.append(
            {
                "archetype": group[0],
                "gender": group[1],
                "guest_count": len(guests),
                "image_count": len(images),
                "minimum": min(usage.values()),
                "maximum": max(usage.values()),
            }
        )
    changed_paths = []
    records = []
    updates = {}
    for path, original in originals.items():
        before = parsed[path]
        after = copy.deepcopy(before)
        root = yaml.compose(original)
        heroes_node = next((v for k, v in root.value if k.value == "heroes"), None)
        if heroes_node is None:
            continue
        edits = []
        for rarity_node, group_node in heroes_node.value:
            rarity = rarity_node.value
            if rarity not in RARITIES:
                continue
            for guest, guest_node in zip(after["heroes"][rarity], group_node.value):
                key = guest["key"]
                avatar = allocations[key]
                assert (SOURCES / avatar).is_file(), avatar
                record = {
                    "key": key,
                    "name": guest["name"],
                    "rarity": rarity,
                    "archetype": guest["archetype"],
                    "gender": guest.get("default_gender", "unknown"),
                    "recruitable": guest.get("recruitable", True),
                    "file": path.relative_to(ROOT).as_posix(),
                    "old_avatar": guest.get("avatar"),
                    "avatar": avatar,
                    "changed": guest.get("avatar") != avatar,
                    "nonhuman": key in NONHUMANS,
                }
                records.append(record)
                if guest.get("avatar") == avatar:
                    continue
                guest["avatar"] = avatar
                fields = {k.value: v for k, v in guest_node.value}
                if "avatar" in fields:
                    node = fields["avatar"]
                    edits.append((node.start_mark.index, node.end_mark.index, avatar))
                else:
                    node = fields["name"]
                    end = original.index("\n", node.end_mark.index) + 1
                    indent = next(k.start_mark.column for k, v in guest_node.value if k.value == "name")
                    edits.append((end, end, " " * indent + "avatar: " + avatar + "\n"))
        output = original
        for start, end, replacement in sorted(edits, reverse=True):
            output = output[:start] + replacement + output[end:]
        assert yaml.safe_load(output) == after, path
        # 验证仅绿蓝角色的 avatar 发生改变，其他品质和所有属性完整保留。
        stripped = copy.deepcopy(after)
        old_stripped = copy.deepcopy(before)
        for rarity in RARITIES:
            for guest in stripped.get("heroes", {}).get(rarity, []):
                guest.pop("avatar", None)
            for guest in old_stripped.get("heroes", {}).get(rarity, []):
                guest.pop("avatar", None)
        assert stripped == old_stripped, path
        if edits:
            changed_paths.append(path)
            updates[path] = output
    backup = REPORT / "original_rosters.zip"
    if backup.exists():
        raise RuntimeError("已有原始备份，拒绝覆盖；请使用已有记录核对")
    with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in changed_paths:
            archive.write(path, path.relative_to(ROOT).as_posix())
    for path in changed_paths:
        assert path.read_text() == originals[path], (path, "出现并发修改")
    for path, output in updates.items():
        path.write_text(output)
    usage = Counter(r["avatar"] for r in records if not r["nonhuman"])
    audit = {
        "scope": ["green", "blue"],
        "total": len(records),
        "changed": sum(r["changed"] for r in records),
        "generic_image_count": len(usage),
        "nonhuman_count": len(NONHUMANS),
        "file_count": len(changed_paths),
        "groups": group_stats,
        "images": [
            {"avatar": image, "archetype": group[0], "gender": group[1], "count": usage[image]}
            for group, images in sorted(catalog.items())
            for image in images
        ],
        "records": records,
    }
    (REPORT / "manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in audit.items() if k not in {"images", "records"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
