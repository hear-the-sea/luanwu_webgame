"""重建个人任务奖励v2；保留任务结构与历史武林箱，可重复执行。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = {
    "fengbang_tianmen": ("zhuxian_sword_intent", "诛仙剑意", "equip_zhuxianjian", 30),
    "zhuolu_zhongyuan": ("xuanyuan_sword_intent", "轩辕剑意", "equip_xuanyuanjian", 40),
    "guandu_zhizhan": ("guandu_emperor_fragment", "官渡帝王战章", "equip_diwangkui", 40),
    "huashan_lunjian_advanced": ("huashan_sword_fragment", "华山重剑心得", "equip_xuantiechongjian", 40),
    "hulao_qunying_zhan_lvbu": ("hulao_halberd_fragment", "虎牢画戟战魂", "equip_fangtianhuaji", 40),
    "baimenlou_mingyun_jueze": ("baimenlou_dragon_fragment", "白门楼盘龙战魂", "equip_panlonggun", 40),
}


def fixed(key, quantity):
    return {"item_key": key, "min_quantity": quantity, "max_quantity": quantity}


def group(label, chance, keys):
    return {
        "label": label,
        "chance": chance,
        "min_quantity": 1,
        "max_quantity": 1,
        "choices": [{"item_key": key, "weight": weight} for key, weight in keys],
    }


def main():
    path = ROOT / "data/item_templates.yaml"
    original = path.read_text()
    items = {x["key"]: x for x in yaml.safe_load(original)["items"]}
    missions = {x["key"]: x for x in yaml.safe_load((ROOT / "data/mission_templates.yaml").read_text())["missions"]}
    recipes = {
        x["blueprint_key"]: x for x in yaml.safe_load((ROOT / "data/forge_blueprints.yaml").read_text())["recipes"]
    }
    changed = {}
    for key, source in items.items():
        if not key.startswith("mission_chest_"):
            continue
        box = deepcopy(source)
        payload = box["effect_payload"]
        mission = missions[payload["mission_key"]]
        mk = mission["key"]
        # 仅替换本版本新增补给与累积碎片，避免重复执行叠加。
        fixed_rewards = [
            x
            for x in payload["item_rewards"]
            if x["item_key"] not in {"tong", "xi", "tie", "zhixuesan", "buxuedan", *(v[0] for v in COLLECTION.values())}
        ]
        groups = payload["random_item_groups"]
        payload["reward_balance_version"] = 2
        tier = mission["difficulty"]
        medicine, amount = ("zhixuesan", 2) if tier == "junior" else ("buxuedan", 2 if tier == "intermediate" else 4)
        fixed_rewards.append(fixed(medicine, amount))
        if mk not in {
            "taozi_fenban",
            "tianpeng_dijiao_1",
            "tianpeng_dijiao_2",
            "mid_autumn_moon_palace_trial",
            "kasukabe_daisakusen",
        }:
            quantities = {"junior": (5, 2, 1), "intermediate": (8, 5, 3), "advanced": (12, 8, 5)}[tier]
            fixed_rewards += [fixed(k, q) for k, q in zip(("tong", "xi", "tie"), quantities)]
        if mk in {"biyou_candeng", "shier_xianyin"}:
            token = "wanyin_flag_fragment" if mk == "biyou_candeng" else "yuxu_broken_seal"
            groups[:] = [g for g in groups if not any(x["item_key"] == token for x in g["choices"])]
            fixed_rewards = [x for x in fixed_rewards if x["item_key"] != token] + [fixed(token, 1)]
            payload["reward_theme"] = {
                "biyou_candeng": "封神入口：必得阵旗碎片，有机会获得校尉军备与狐仙图纸",
                "shier_xianyin": "封神中层：必得玉虚残印，有机会获得阵法秘术与太清玉册",
            }[mk]
        if mk == "jiguanshou_chuxian":
            groups[:] = [
                group(
                    "机关入门",
                    0.8,
                    [
                        ("blueprint_xiaoxingjiguanshu", 3),
                        ("blueprint_tongchanjiguan", 3),
                        ("blueprint_jixiemao", 1),
                        ("equip_qingtongsuanchou", 3),
                    ],
                )
            ]
        if mk == "biwu_zhaoqin":
            next(g for g in groups if g["choices"][0]["item_key"] == "blueprint_jiguanxiong")["chance"] = 0.05
        if mk == "jiguan_chuniao":
            fixed_rewards = [x for x in fixed_rewards if x["item_key"] != "blueprint_jiguanchuniao"]
            groups[:] = [
                g
                for g in groups
                if g["label"] not in {"blueprint_jiguanchuniao", "机关雏鸟主图纸", "blueprint_mechanism_blue"}
            ]
            groups += [
                group("机关雏鸟主图纸", 0.4, [("blueprint_jiguanchuniao", 1)]),
                group(
                    "blueprint_mechanism_blue",
                    0.2,
                    [("blueprint_jixiemao", 1), ("blueprint_kuileimuren", 1), ("blueprint_qingji", 1)],
                ),
            ]
            payload["reward_theme"] = "中期机关养成：雏鸟图纸与随图纸发放的锻造材料"
        if mk == "fengbang_tianmen":
            groups[:] = [g for g in groups if g["label"] != "封神至宝"]
            groups.append(
                group("封神至宝", 0.15, [("equip_taiqingyuce", 1), ("equip_fengshenbang", 1), ("equip_zhuxianjian", 1)])
            )
        if mk == "zhuolu_zhongyuan":
            old = next(g for g in groups if g["label"] == "上古王权")
            old["chance"] = 0.15
            for c in old["choices"]:
                c["weight"] = 1
        if mk in {"fengbang_tianmen", "zhuolu_zhongyuan"}:
            next(g for g in groups if g["label"] == "blueprint_top_orange")["chance"] = 0.06
        if mk == "guandu_zhizhan":
            old = next(g for g in groups if g["label"] in {"官渡军备", "官渡橙色军备"})
            orange = [
                (c["item_key"], c["weight"]) for c in old["choices"] if items[c["item_key"]]["rarity"] == "orange"
            ]
            groups[:] = [g for g in groups if g["label"] not in {"官渡军备", "官渡橙色军备", "曹氏名驹"}]
            groups += [
                group("官渡橙色军备", 0.15, orange),
                group("曹氏名驹", 0.25, [("equip_jueying", 1), ("equip_zhaohuangfeidian", 1)]),
            ]
        if mk == "huashan_lunjian_advanced":
            old = next(g for g in groups if g["label"] == "武林名器")
            for c in old["choices"]:
                c["weight"] = 27 if items[c["item_key"]]["rarity"] == "orange" else 85
        if mk == "hulao_qunying_zhan_lvbu":
            old = next(g for g in groups if g["label"] == "主题装备")
            old["chance"] = 0.10
            for c in old["choices"]:
                c["weight"] = 1
        if mk == "baimenlou_mingyun_jueze":
            groups[:] = [g for g in groups if g["label"] not in {"主题装备", "白门楼橙色名器", "白门楼紫色军备"}]
            groups += [
                group("白门楼橙色名器", 0.1, [("equip_panlonggun", 1)]),
                group("白门楼紫色军备", 0.35, [("equip_pojunzhanjia", 1), ("equip_tianjiguan", 1)]),
            ]
        if mk in COLLECTION:
            token, name, target, threshold = COLLECTION[mk]
            fixed_rewards.append(fixed(token, 1))
            changed[token] = {
                "key": token,
                "name": name,
                "description": f"每次开启{box['name']}必得1个；集齐{threshold}个后在仓库使用，消耗{threshold}个兑换{items[target]['name']}×1。不可交易。",
                "effect_type": "tool",
                "rarity": "orange",
                "tradeable": False,
                "price": 0,
                "storage_space": 1,
                "is_usable": True,
                "image": f"{token}_v1.png",
                "effect_payload": {
                    "action": "synthesize_item",
                    "result_item_key": target,
                    "result_quantity": 1,
                    "required_items": {token: threshold - 1},
                    "collection_cost": threshold,
                    "confirm_title": f"兑换{items[target]['name']}",
                    "confirm_text": f"确认消耗{threshold}个{name}，兑换{items[target]['name']}？",
                    "confirm_ok_text": "确认兑换",
                },
            }
        payload["item_rewards"] = fixed_rewards
        # 只随实际命中的器械图纸发料，floor避免小数量材料向上取整膨胀。
        keys = {x["item_key"] for x in fixed_rewards} | {c["item_key"] for g in groups for c in g["choices"]}
        support = {
            bp: [fixed(k, q // 2) for k, q in recipes[bp]["costs"].items() if q // 2 > 0]
            for bp in sorted(keys & recipes.keys())
            if items[recipes[bp]["result_item_key"]]["effect_type"] == "equip_device"
        }
        payload["blueprint_material_support"] = support
        payload["blueprint_forging_requirements"] = {bp: recipes[bp]["required_forging"] for bp in support}
        box["description"] = (
            payload["reward_theme"] + "。必得基础经验与补给，其他奖励按独立组抽取；在仓库使用开启，不可交易，占仓容5。"
        )
        if mk in {"biyou_candeng", "shier_xianyin", "sishui_huaxiong_chengwei", "hulao_qunying_zhan_lvbu"}:
            box["description"] += "下一关凭证必得1，开箱后即可用于挑战。"
        if support:
            box["description"] += "器械只出图纸，命中时附带配方各材料一半（向下取整）；剩余材料与锻造等级仍需满足。"
        if mk in COLLECTION:
            box["description"] += f"必得{name}×1，{threshold}个可在仓库兑换{items[target]['name']}。"
        changed[key] = box
    blocks = re.split(r"(?=^  - key: )", original, flags=re.M)
    written = set()
    for i, block in enumerate(blocks):
        match = re.match(r"  - key: (\S+)", block)
        if not match or match[1] not in changed:
            continue
        key = match[1]
        written.add(key)
        rendered = yaml.safe_dump({"items": [changed[key]]}, allow_unicode=True, sort_keys=False, width=120).split(
            "items:\n", 1
        )[1]
        blocks[i] = "\n".join("  " + line for line in rendered.splitlines()) + "\n\n"
    text = "".join(blocks)
    for key in changed.keys() - written:
        rendered = yaml.safe_dump({"items": [changed[key]]}, allow_unicode=True, sort_keys=False, width=120).split(
            "items:\n", 1
        )[1]
        text += "\n".join("  " + line for line in rendered.splitlines()) + "\n\n"
    path.write_text(text.rstrip() + "\n")
    report = ROOT / "reports/mission_reward_design_20261001.json"
    plan = json.loads(report.read_text())
    plan["reward_balance_version"] = 2
    for row in plan["missions"]:
        row["theme"] = changed[row["chest_key"]]["effect_payload"]["reward_theme"]
    report.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    print("32任务v2奖励更新，6种累计兑换碎片；历史箱保持")


if __name__ == "__main__":
    main()
