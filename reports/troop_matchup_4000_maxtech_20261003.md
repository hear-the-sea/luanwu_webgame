# 4000 箭神对拳圣、枪王胜率模拟（2026-10-03）

使用当前正式 battle.simulation 引擎，battle_engine_version=3、rng_version=1。
双方完全相同的20名100级可招募橙色门客；按基础攻击降序、模板key升序选取前20名。
门客成长随机种子20261003，成长属性生成一次后双方使用同一快照。
使用模板初始技能；数据库中这20名门客的初始技能均为空，未附加学习技能。
无装备、无护院器械、无城防。所有科技按各自max_level取满。
数据库共21种护院，主力4000名，其余20种各1名；每方4020名护院。
每组1000场，种子100000至100999，使用正式combat随机子流。
交换攻守，各兵种对比共2000场，两组共4000场。

| 对手 | 箭神位置 | 箭神胜场 | 对手胜场 | 箭神胜率 | 平均回合 | 到上限未决场数 |
|---|---|---:|---:|---:|---:|---:|
| 拳圣 | 进攻 | 268 | 732 | 26.8% | 13.775 | 0 |
| 拳圣 | 防守 | 370 | 630 | 37.0% | 14.158 | 0 |
| 枪王 | 进攻 | 613 | 387 | 61.3% | 10.139 | 0 |
| 枪王 | 防守 | 642 | 358 | 64.2% | 10.232 | 0 |

攻守各占一半时：拳圣对箭神胜率68.1%；箭神对枪王胜率62.75%。

满科技核心属性（单兵）：箭神攻击30、防御12、生命90、敏捷18；拳圣攻击40、防御14、生命130、敏捷38；枪王攻击44、防御20、生命144、敏捷16。
拳克弓、弓克枪，克制攻击倍率1.5。拳圣远程防御+100%、远程减伤20%，行动前50%概率回血，进攻恢复当前生命10%、防守13%。箭神有85%伤害的提前齐射阶段，近战攻击+100%。枪王有50%概率反击、反击系数30%。

这属于指定阵容的条件胜率；改变门客、技能、装备、护院器械或城防需要另行模拟。每种攻守组合1000场，比例抽样误差约±3个百分点（95%近似区间）。

门客阵容：阿青、独孤求败、柴玉关、扫地僧、石破天、燕南天、朱元璋、刘彻、刘秀、项羽、曹操、刘裕、拓跋焘、关羽、赵匡胤、铁木真、忽必烈、李世民、杨坚、嬴政。

完整科技等级与模拟统计：

```json
{
  "roster": [
    "阿青",
    "独孤求败",
    "柴玉关",
    "扫地僧",
    "石破天",
    "燕南天",
    "朱元璋",
    "刘彻",
    "刘秀",
    "项羽",
    "曹操",
    "刘裕",
    "拓跋焘",
    "关羽",
    "赵匡胤",
    "铁木真",
    "忽必烈",
    "李世民",
    "杨坚",
    "嬴政"
  ],
  "tech_levels": {
    "scout_art": 200,
    "march_art": 10,
    "architecture": 10,
    "horsemanship": 10,
    "animal_husbandry": 10,
    "smelting": 10,
    "forging": 10,
    "dao_attack": 10,
    "dao_defense": 10,
    "dao_agility": 10,
    "dao_hp": 10,
    "dao_double_strike": 5,
    "qiang_attack": 10,
    "qiang_defense": 10,
    "qiang_agility": 10,
    "qiang_hp": 10,
    "qiang_siege": 10,
    "qiang_counter": 5,
    "jian_attack": 10,
    "jian_defense": 10,
    "jian_agility": 10,
    "jian_hp": 10,
    "jian_reflect": 5,
    "jian_preempt": 5,
    "quan_attack": 10,
    "quan_defense": 10,
    "quan_agility": 10,
    "quan_hp": 10,
    "quan_vs_ranged": 10,
    "quan_heal": 5,
    "gong_attack": 10,
    "gong_defense": 10,
    "gong_agility": 10,
    "gong_hp": 10,
    "gong_range": 5,
    "gong_melee": 10,
    "farming": 30,
    "dao_recruit": 7,
    "qiang_recruit": 7,
    "jian_recruit": 7,
    "quan_recruit": 7,
    "gong_recruit": 7
  },
  "results": [
    {
      "opponent": "quan_sheng",
      "arrow_side": "attacker",
      "n": 1000,
      "wins": {
        "arrow": 268,
        "opponent": 732
      },
      "rounds_mean": 13.775,
      "timeouts": 0,
      "main_troop_survivors_mean": {
        "arrow_god": 68.976,
        "quan_sheng": 1991.725
      }
    },
    {
      "opponent": "quan_sheng",
      "arrow_side": "defender",
      "n": 1000,
      "wins": {
        "arrow": 370,
        "opponent": 630
      },
      "rounds_mean": 14.158,
      "timeouts": 0,
      "main_troop_survivors_mean": {
        "arrow_god": 99.395,
        "quan_sheng": 1546.189
      }
    },
    {
      "opponent": "qiang_wang",
      "arrow_side": "attacker",
      "n": 1000,
      "wins": {
        "opponent": 387,
        "arrow": 613
      },
      "rounds_mean": 10.139,
      "timeouts": 0,
      "main_troop_survivors_mean": {
        "arrow_god": 1187.544,
        "qiang_wang": 351.708
      }
    },
    {
      "opponent": "qiang_wang",
      "arrow_side": "defender",
      "n": 1000,
      "wins": {
        "arrow": 642,
        "opponent": 358
      },
      "rounds_mean": 10.232,
      "timeouts": 0,
      "main_troop_survivors_mean": {
        "arrow_god": 1237.281,
        "qiang_wang": 322.996
      }
    }
  ]
}
```
