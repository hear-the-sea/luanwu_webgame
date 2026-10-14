"""重建当前个人任务宝箱；保留旧版奖励与战斗经验果直发规则，可重复执行。"""

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
COLLECTION_DESCRIPTIONS = {
    "zhuxian_sword_intent": "仙剑余威未散，攒够便能请它出山。",
    "xuanyuan_sword_intent": "一缕轩辕剑意，离神剑又近一步。",
    "guandu_emperor_fragment": "官渡一战的功勋，攒够换顶威风的盔。",
    "huashan_sword_fragment": "重剑无锋，高手的心得也得慢慢攒。",
    "hulao_halberd_fragment": "虎牢关的战意未散，画戟正等着新主人。",
    "baimenlou_dragon_fragment": "龙吟藏在战魂里，攒够便能唤出盘龙棍。",
}
CHEST_DESCRIPTIONS = {
    "mid_autumn_moon_palace_trial": "月宫打包的小惊喜，嫦娥说别偷吃。",
    "kasukabe_daisakusen": "小新的神秘收藏，打开前先喊一声：动感光波！",
    "huashan_lunjian": "嘴上论贱，手里论宝。打开看看谁更有料！",
    "wagangzhai_nixi": "守住了庄园，也守住了这箱好东西。",
    "jingyanggang": "打虎归来，喝口酒，开个箱！",
    "wulongshan": "山贼攒了半年的家当，今天归你开箱。",
    "wagangzhai": "瓦岗好汉的私房宝贝，别告诉程咬金。",
    "fugui_shanzhuang": "富贵人家的小收藏，连箱子都透着阔气。",
    "shizipo_heidian": "掌柜说这是贵客专享，开箱时记得留个心眼。",
    "shanhaiguan": "守关将士的压箱底，够硬气，也够惊喜。",
    "shiren_daochang": "十个人的考验，一箱子的修行心得。",
    "jiguanshou_chuxian": "咔哒咔哒，机关师的小秘密都在里面。",
    "biwu_zhaoqin": "擂台赢了，宝箱拿了。姻缘嘛，再说！",
    "tianpeng_dijiao_1": "天蓬偷偷藏的家底，轻点开，别惊动他。",
    "tianpeng_dijiao_2": "藏得越深，越舍不得给人看。嘘，快开！",
    "jiguan_chuniao": "小机关，大梦想。开箱给你的巧手添点灵感！",
    "dongtian_fudi": "洞里一天，箱里惊喜。修行也要偶尔拆个礼物。",
    "jiufeng_feihuan": "翻过九座山，总得带点好东西回去。",
    "taozi_fenban": "桃子分你一半，快乐可不止一半！",
    "simian_chuge": "楚歌唱得响，霸王的家底藏得深。",
    "manzu_ruqin": "来时气势汹汹，走时留下满箱战利品。",
    "zhuiji_manzu": "追得够快，宝贝就来不及被带走！",
    "liangshanbo_zhuyingtai": "一箱情意，几分缘分。好人也有好礼收！",
    "biyou_candeng": "灯还没灭，仙家藏的好东西先归你瞧瞧。",
    "shier_xianyin": "十二道仙印封着的小秘密，轮到你揭开了。",
    "fengbang_tianmen": "天门已开，仙家宝库也该分你一份！",
    "zhuolu_zhongyuan": "逐鹿归来，开箱看看上古英雄的收藏。",
    "guandu_zhizhan": "曹军的压箱底，这回不用借东风也能拿。",
    "huashan_lunjian_advanced": "论剑论到最后，高手们把私藏也交出来了。",
    "sishui_huaxiong_chengwei": "华雄守关没守住，倒替你看好了这箱宝贝。",
    "hulao_qunying_zhan_lvbu": "打过虎牢关，开箱也得有点英雄气！",
    "baimenlou_mingyun_jueze": "故事到了白门楼，箱里的缘分还没结束。",
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
    active_chest_keys = {
        key for mission in missions.values() for key in mission["drop_table"] if key.startswith("mission_chest_v3_")
    }
    recipes = {
        x["blueprint_key"]: x for x in yaml.safe_load((ROOT / "data/forge_blueprints.yaml").read_text())["recipes"]
    }
    changed = {}
    for key, source in items.items():
        if key not in active_chest_keys:
            continue
        box = deepcopy(source)
        payload = box["effect_payload"]
        mission = missions[payload["mission_key"]]
        mk = mission["key"]
        # 仅替换本版本新增补给与累积碎片，避免重复执行叠加。
        fixed_rewards = [
            x
            for x in payload["item_rewards"]
            if x["item_key"]
            not in {
                "experience_fruit",
                "tong",
                "xi",
                "tie",
                "zhixuesan",
                "buxuedan",
                *(v[0] for v in COLLECTION.values()),
            }
        ]
        groups = payload["random_item_groups"]
        payload["reward_balance_version"] = 3
        payload["experience_fruit_source"] = "battle_salvage"
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
                "description": (
                    f"{COLLECTION_DESCRIPTIONS[token]}" f"集齐{threshold}个，在仓库使用可兑换{items[target]['name']}。"
                ),
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
        box["description"] = CHEST_DESCRIPTIONS[mk]
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
    plan["reward_balance_version"] = 3
    plan["xp_assumption"] = "经验果按实际战报回收量直接发放；宝箱保留经验桃、菠萝、西瓜；旧版宝箱奖励不变"
    for row in plan["missions"]:
        row["chest_key"] = next(key for key in missions[row["mission_key"]]["drop_table"] if key in active_chest_keys)
        row["theme"] = changed[row["chest_key"]]["effect_payload"]["reward_theme"]
        row["experience_fruit_source"] = "battle_salvage"
        row["legacy_fixed_experience_fruit"] = row.pop(
            "fixed_experience_fruit", row.get("legacy_fixed_experience_fruit", 0)
        )
    report.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    print("32任务当前宝箱更新，经验果由实际战斗直接结算；历史箱保持")


if __name__ == "__main__":
    main()
