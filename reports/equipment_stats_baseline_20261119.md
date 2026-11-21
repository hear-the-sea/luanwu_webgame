# 装备属性基线（原数值）— 2026-11-19

> 数据来源：`data/item_templates.yaml`（`effect_type: equip_*` 的 `effect_payload`）。
> 统计口径：共 **278 件**装备，其中词缀变体由运行时按 5:1 转换生成（不在本表内）。
> 本文件只记录现状，不含调整建议；调整后数值见 `equipment_stats_adjusted_20261119.md`。

## 一、属性作用速查

| 字段 | 作用 | 关键公式 |
| --- | --- | --- |
| hp | 生命上限 | `max(200, base_hp + hp_bonus + defense×50) + (lv-1)×60` |
| force | 武力 | 攻击 += force×0.7（武将）/×0.5（文官） |
| intellect | 智力 | 攻击 += intellect×0.3（武将）/×0.5（文官） |
| defense | 防御 | 减伤 `def/(def+K)`；门客对门客 K=600，门客对小兵 K=50；同时提供 50 倍 HP |
| agility | 敏捷 | 仅影响行动顺序（暴击固定 5%） |
| luck | 运势 | 主动技能触发率 `clamp(0.95×(luck/300)^0.85, 5%, 95%)` |
| troop_capacity | 带兵量 | 门客可携带护院数 = 200（lv70+50）+ 该值 |
| troop_stat_bonus | 器械兵种加成 | 按兵种给 attack/defense/hp 百分比加成 |
| set_bonus | 套装加成 | `attack` 加 attack_bonus，`defense` 加 defense_stat，其余同上 |

## 二、总量分布

| 槽位 | 白 | 绿 | 蓝 | 紫 | 橙 | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 头盔 | 4 | 8 | 9 | 6 | 3 | 30 |
| 护甲 | 4 | 8 | 8 | 13 | 6 | 39 |
| 武器 | 11 | 16 | 16 | 23 | 15 | 81 |
| 鞋子 | 4 | 8 | 8 | 6 | 4 | 30 |
| 饰品 | 0 | 14 | 14 | 14 | 10 | 52 |
| 坐骑 | 4 | 6 | 6 | 6 | 7 | 29 |
| 器械 | 0 | 3 | 5 | 6 | 3 | 17 |
| **合计** | 27 | 63 | 66 | 74 | 48 | 278 |

| 稀有度 | 数量 | 等级中位数 | 生命中位 | 武力中位 | 智力中位 | 防御中位 | 敏捷中位 | 运势中位 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 白 | 27 | 5 | 100 | 5 | 0 | 5 | 5 | 0 |
| 绿 | 63 | 15 | 120 | 5 | 0 | 5 | 4 | 0 |
| 蓝 | 66 | 30.0 | 210 | 12 | 0 | 9 | 10 | 0 |
| 紫 | 74 | 55.0 | 260 | 16 | 0 | 12.5 | 0 | 0 |
| 橙 | 48 | 75.0 | 276.5 | 20 | 0 | 7.5 | 12 | 8 |

## 三、套装总表

| 套装 | 件数 | 稀有度 | 成员 | 套装效果 |
| --- | ---: | --- | --- | --- |
| `kasukabe_defense_set` | 5 | 蓝 | equip_action_mask, equip_shiro_leash, equip_kasukabe_satchel, equip_action_beam_glove, equip_nohara_pan | 2件: 防御+15、攻击+20；4件: 生命+400、敏捷+15、运势+10 |
| `starter_cloth_set` | 4 | 白 | equip_caomao, equip_suoyi, equip_caoxie, equip_tiejian | 4件: 生命+200、武力+20、带兵+20、运势+15 |
| `yeman_set` | 2 | 绿 | equip_yemantoukie, equip_yemankandao | 2件: 生命+250、武力+20、防御+8 |
| `xiaowei_set` | 4 | 蓝 | equip_xiaoweitoukui, equip_xiaoweikaijia, equip_xiaoweichangxue, equip_xiaoweichangjian | 4件: 生命+300、防御+12、武力+30、带兵+50 |
| `jiaodoushi_set` | 4 | 蓝 | equip_jiaodoushitoukui, equip_jiaodoushixiongjia, equip_jiaodoushizhixue, equip_jiaodoushizhichui | 4件: 生命+240、防御+8、武力+24、敏捷+12 |
| `sixiang_set` | 4 | 蓝 | equip_qinglongkui, equip_qinglongjia, equip_qinglongxue, equip_qinglongdao | 4件: 生命+300、武力+25、智力+25、敏捷+15 |
| `qilin_set` | 5 | 紫 | equip_qilinkui, equip_qilinjia, equip_qilinxue, equip_qilindao, equip_qilinzui | 4件: 生命+320、武力+28、敏捷+12、运势+20 |
| `fengling_set` | 3 | 紫 | equip_fenglingguan, equip_fenglingshan, equip_fenghuangyu | 3件: 智力+40、运势+20 |
| `xuanwu_set` | 4 | 紫 | equip_xuanwukui, equip_xuanwujia, equip_xuanwuxue, equip_xuanwuchui | 4件: 生命+220、防御+40、武力+12 |
| `nichang_set` | 3 | 蓝/绿 | equip_nichangyuyi, equip_nichangwuxie, equip_nichangjian | 3件: 生命+120、智力+15、敏捷+8、运势+12 |
| `huxian_set` | 3 | 紫 | equip_huxianpao, equip_huxianxie, equip_huxianjian | 3件: 生命+220、智力+45、运势+15 |

## 四、逐件原数值

### 头盔（30 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_bumao` | 布帽 | 白 | 1 | 生命100 / 智力8 / 防御5 / 敏捷5 | — | 锻造 | 100 |
| `equip_caomao` | 斗笠 | 白 | 1 | 生命250 / 武力5 / 防御10 | starter_cloth_set | 其他/掉落 | 0 |
| `equip_niupimao` | 牛皮帽 | 白 | 10 | 生命100 / 武力5 / 防御10 / 敏捷5 | — | 锻造 | 300 |
| `equip_zhubiankui` | 竹编盔 | 白 | 10 | 生命300 / 防御15 | — | 其他/掉落 | 200 |
| `equip_baihongkui` | 白虹盔 | 绿 | 10 | 生命160 / 武力4 / 防御12 / 带兵10 | — | 锻造 | 3000 |
| `equip_tieyekui` | 铁叶盔 | 绿 | 10 | 生命120 / 武力12 / 防御8 | — | 锻造 | 800 |
| `equip_yulindin` | 鱼鳞顶 | 绿 | 10 | 生命250 / 武力1 / 防御12 / 敏捷4 | — | 锻造 | 1500 |
| `equip_qingtengguan` | 青藤冠 | 绿 | 15 | 生命200 / 智力15 / 防御10 / 运势5 | — | 其他/掉落 | 2600 |
| `equip_yemantoukie` | 野蛮头盔 | 绿 | 15 | 生命150 / 武力16 / 防御10 | yeman_set | 其他/掉落 | 2000 |
| `equip_baoutoukui` | 豹头盔 | 绿 | 20 | 生命200 / 防御18 / 敏捷12 | — | 其他/掉落 | 2800 |
| `equip_huwenkui` | 虎纹盔 | 绿 | 20 | 生命220 / 武力15 / 防御18 | — | 其他/掉落 | 2500 |
| `equip_yanlingmao` | 雁翎帽 | 绿 | 20 | 生命200 / 防御15 / 敏捷12 | — | 其他/掉落 | 2200 |
| `equip_action_mask` | 动感超人面具 | 蓝 | 25 | 生命300 / 武力18 / 防御5 / 敏捷15 | kasukabe_defense_set | 其他/掉落 | 6000 |
| `equip_xiaoweitoukui` | 校尉头盔 | 蓝 | 30 | 生命300 / 武力15 / 防御20 / 带兵15 | xiaowei_set | 图纸 | 5000 |
| `equip_yunwenkui` | 云纹盔 | 蓝 | 30 | 生命300 / 智力22 / 防御15 / 运势10 | — | 其他/掉落 | 6200 |
| `equip_feiyukui` | 飞羽盔 | 蓝 | 35 | 生命300 / 防御15 / 敏捷15 / 运势10 | — | 其他/掉落 | 5600 |
| `equip_jiaodoushitoukui` | 角斗士头盔 | 蓝 | 35 | 生命300 / 武力18 / 防御25 | jiaodoushi_set | 其他/掉落 | 6200 |
| `equip_qinglongkui` | 青龙盔 | 蓝 | 35 | 生命300 / 智力22 / 防御20 | sixiang_set | 图纸 | 5500 |
| `equip_hantiekui` | 寒铁盔 | 蓝 | 40 | 生命300 / 武力5 / 防御26 | — | 其他/掉落 | 6500 |
| `equip_langyakui` | 狼牙盔 | 蓝 | 40 | 生命300 / 武力18 / 防御20 / 敏捷15 | — | 其他/掉落 | 5800 |
| `equip_xuantiekui` | 玄铁盔 | 蓝 | 40 | 生命300 / 武力15 / 防御26 | — | 其他/掉落 | 6000 |
| `equip_tianjiguan` | 天机冠 | 紫 | 45 | 生命360 / 智力28 / 防御20 / 运势12 | — | 其他/掉落 | 14500 |
| `equip_fenglingguan` | 凤翎冠 | 紫 | 55 | 生命360 / 智力28 / 防御21 / 运势11 | fengling_set | 图纸 | 14000 |
| `equip_pojunkui` | 破军盔 | 紫 | 55 | 生命360 / 武力24 / 防御26 / 敏捷15 / 带兵20 | — | 其他/掉落 | 15500 |
| `equip_qilinkui` | 麒麟盔 | 紫 | 55 | 生命360 / 武力24 / 智力3 / 防御32 | qilin_set | 图纸 | 15000 |
| `equip_xuanwukui` | 玄武盔 | 紫 | 55 | 生命360 / 武力10 / 智力10 / 防御32 | xuanwu_set | 图纸 | 16000 |
| `equip_bawangkui` | 霸王盔 | 紫 | 60 | 生命360 / 武力24 / 防御30 / 带兵15 | — | 其他/掉落 | 18000 |
| `equip_longhuangguan` | 龙皇冠 | 橙 | 65 | 生命400 / 武力12 / 智力30 / 防御26 / 带兵40 | — | 其他/掉落 | 50000 |
| `equip_taixudiguan` | 太虚帝冠 | 橙 | 75 | 生命400 / 智力40 / 防御20 / 运势14 | — | 其他/掉落 | 55000 |
| `equip_diwangkui` | 帝王盔 | 橙 | 80 | 生命400 / 武力40 / 智力10 / 防御32 / 带兵30 | — | 其他/掉落 | 48000 |

### 护甲（39 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_bupao` | 布袍 | 白 | 1 | 生命80 / 智力8 / 防御6 | — | 锻造 | 100 |
| `equip_suoyi` | 蓑衣 | 白 | 1 | 生命100 / 武力5 / 防御10 / 敏捷5 | starter_cloth_set | 其他/掉落 | 0 |
| `equip_shengpijia` | 生皮甲 | 白 | 5 | 生命160 / 武力4 / 防御12 | — | 锻造 | 300 |
| `equip_cubujia` | 粗布甲 | 白 | 10 | 生命150 / 武力5 / 防御10 / 敏捷5 | — | 其他/掉落 | 200 |
| `equip_housipao` | 厚丝袍 | 绿 | 10 | 生命80 / 智力4 / 防御12 / 敏捷6 | — | 锻造 | 800 |
| `equip_shapijia` | 鲨皮甲 | 绿 | 10 | 生命200 / 武力4 / 防御14 / 敏捷4 | — | 锻造 | 1500 |
| `equip_nichangyuyi` | 霓裳羽衣 | 绿 | 15 | 生命50 / 智力15 / 防御15 / 运势8 | nichang_set | 其他/掉落 | 2800 |
| `equip_qingzhujia` | 青竹甲 | 绿 | 15 | 生命200 / 武力5 / 防御20 / 敏捷10 | — | 其他/掉落 | 2200 |
| `equip_shanwenjia` | 山纹甲 | 绿 | 15 | 生命500 / 武力10 / 防御20 | — | 其他/掉落 | 2800 |
| `equip_tiexianjia` | 铁线甲 | 绿 | 15 | 生命500 / 武力12 / 防御15 | — | 其他/掉落 | 3000 |
| `equip_suozijia` | 锁子甲 | 绿 | 20 | 生命500 / 武力5 / 防御22 | — | 其他/掉落 | 2000 |
| `equip_xuantiejia` | 玄铁甲 | 绿 | 20 | 生命500 / 防御22 | — | 其他/掉落 | 2500 |
| `equip_kasukabe_satchel` | 春日部小书包 | 蓝 | 25 | 生命400 / 防御20 / 敏捷10 / 运势10 | kasukabe_defense_set | 其他/掉落 | 6200 |
| `equip_yunwenfayi` | 云纹法衣 | 蓝 | 25 | 生命350 / 智力25 / 防御10 / 运势10 | — | 其他/掉落 | 6200 |
| `equip_wujinjia` | 乌金甲 | 蓝 | 30 | 生命400 / 武力18 / 防御25 | — | 其他/掉落 | 7000 |
| `equip_xiaoweikaijia` | 校尉铠甲 | 蓝 | 30 | 生命400 / 武力10 / 防御25 / 带兵20 | xiaowei_set | 图纸 | 6000 |
| `equip_jiaodoushixiongjia` | 角斗士胸甲 | 蓝 | 35 | 生命400 / 武力18 / 防御28 | jiaodoushi_set | 其他/掉落 | 7600 |
| `equip_linwenzhanjia` | 鳞纹战甲 | 蓝 | 35 | 生命400 / 武力15 / 防御28 | — | 其他/掉落 | 7500 |
| `equip_qinglongjia` | 青龙甲 | 蓝 | 35 | 生命350 / 武力15 / 智力18 / 防御24 | sixiang_set | 图纸 | 6500 |
| `equip_xuanjiazhanbao` | 玄甲战袍 | 蓝 | 40 | 生命400 / 智力20 / 防御28 | — | 其他/掉落 | 8000 |
| `equip_tianjidaopao` | 天机道袍 | 紫 | 45 | 生命376 / 智力28 / 防御19 / 运势15 | — | 其他/掉落 | 17000 |
| `equip_huxianpao` | 狐仙袍 | 紫 | 50 | 生命500 / 武力3 / 智力28 / 防御25 | huxian_set | 图纸 | 15000 |
| `equip_kaiyangkaijia` | 开阳铠甲 | 紫 | 55 | 生命450 / 武力10 / 防御25 / 运势20 | — | 其他/掉落 | 19000 |
| `equip_pojunzhanjia` | 破军战甲 | 紫 | 55 | 生命500 / 武力22 / 防御29 / 带兵20 | — | 其他/掉落 | 19000 |
| `equip_qilinjia` | 麒麟甲 | 紫 | 55 | 生命500 / 武力22 / 智力4 / 防御35 | qilin_set | 图纸 | 18000 |
| `equip_tianjikaijia` | 天玑铠甲 | 紫 | 55 | 生命450 / 智力30 / 防御24 | — | 其他/掉落 | 19000 |
| `equip_tianquankaijia` | 天权铠甲 | 紫 | 55 | 生命500 / 武力10 / 防御40 | — | 其他/掉落 | 19000 |
| `equip_tianshukaijia` | 天枢铠甲 | 紫 | 55 | 生命560 / 武力12 / 防御32 | — | 其他/掉落 | 19000 |
| `equip_tianxuankaijia` | 天璇铠甲 | 紫 | 55 | 生命450 / 武力30 / 防御24 | — | 其他/掉落 | 19000 |
| `equip_xuanwujia` | 玄武甲 | 紫 | 55 | 生命500 / 武力7 / 智力7 / 防御36 | xuanwu_set | 图纸 | 20000 |
| `equip_yaoguangkaijia` | 摇光铠甲 | 紫 | 55 | 生命420 / 防御24 / 敏捷34 | — | 其他/掉落 | 19000 |
| `equip_yuhengkaijia` | 玉衡铠甲 | 紫 | 55 | 生命500 / 武力11 / 智力11 / 防御28 | — | 其他/掉落 | 19000 |
| `equip_tiancanbaoyi` | 天蚕宝衣 | 紫 | 60 | 生命500 / 武力6 / 防御30 / 敏捷18 | — | 其他/掉落 | 16000 |
| `equip_hundunlongpao` | 混沌龙袍 | 橙 | 65 | 生命540 / 智力40 / 防御15 / 运势15 | — | 其他/掉落 | 65000 |
| `equip_jinsirujia` | 金丝软甲 | 橙 | 70 | 生命550 / 防御22 / 敏捷28 / 运势16 | — | 其他/掉落 | 55000 |
| `equip_longlinzhanjia` | 龙鳞战甲 | 橙 | 75 | 生命550 / 武力38 / 防御29 | — | 其他/掉落 | 60000 |
| `equip_shuduanbaijinpao` | 蜀缎白锦袍 | 橙 | 75 | 生命550 / 武力10 / 防御26 / 敏捷30 | — | 其他/掉落 | 60000 |
| `equip_chiyoujia` | 蚩尤甲 | 橙 | 80 | 生命600 / 武力40 / 防御48 / 运势15 | — | 图纸 | 68000 |
| `equip_ruanweijia` | 软猬甲 | 橙 | 80 | 生命550 / 武力8 / 防御48 / 敏捷8 | — | 其他/掉落 | 58000 |

### 武器（81 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_changgong` | 长弓 | 白 | 1 | 武力12 / 敏捷4 | — | 锻造 | 100 |
| `equip_changqiang` | 长枪 | 白 | 1 | 生命160 / 武力9 / 带兵15 | — | 锻造 | 100 |
| `equip_duandao` | 短刀 | 白 | 1 | 生命40 / 武力12 / 敏捷4 | — | 锻造 | 100 |
| `equip_duanjian` | 短剑 | 白 | 1 | 生命80 / 武力8 / 敏捷6 | — | 锻造 | 100 |
| `equip_tiejian` | 铁剑 | 白 | 1 | 武力15 / 防御5 | starter_cloth_set | 其他/掉落 | 0 |
| `equip_changjian` | 长剑 | 白 | 5 | 生命120 / 武力14 / 防御1 | — | 锻造 | 300 |
| `equip_dakandao` | 大砍刀 | 白 | 5 | 生命80 / 武力16 | — | 锻造 | 300 |
| `equip_fanqugong` | 反曲弓 | 白 | 5 | 武力12 / 敏捷6 | — | 锻造 | 300 |
| `equip_baoweiqiang` | 豹尾枪 | 白 | 10 | 武力8 / 防御4 / 带兵25 | — | 锻造 | 300 |
| `equip_changbian` | 长鞭 | 白 | 10 | 武力12 / 敏捷8 | — | 锻造 | 100 |
| `equip_niupibian` | 牛皮鞭 | 白 | 10 | 武力10 / 敏捷6 / 运势4 | — | 锻造 | 300 |
| `equip_duanmajian` | 断马剑 | 绿 | 10 | 武力20 / 敏捷8 | — | 锻造 | 1500 |
| `equip_hutoumao` | 虎头矛 | 绿 | 10 | 武力16 / 防御4 / 带兵15 | — | 锻造 | 800 |
| `equip_jicibian` | 棘刺鞭 | 绿 | 10 | 武力12 / 敏捷6 / 运势6 | — | 锻造 | 800 |
| `equip_jiulonggangbian` | 九龙钢鞭 | 绿 | 10 | 武力12 / 敏捷8 / 运势6 | — | 锻造 | 1500 |
| `equip_pansheqiang` | 盘蛇枪 | 绿 | 10 | 武力16 / 敏捷4 / 带兵20 | — | 锻造 | 1500 |
| `equip_shenbigong` | 神臂弓 | 绿 | 10 | 武力16 / 敏捷12 | — | 锻造 | 1500 |
| `equip_jingtiedao` | 精铁刀 | 绿 | 15 | 生命40 / 武力28 | — | 锻造 | 1500 |
| `equip_mingshejiebian` | 冥蛇节鞭 | 绿 | 15 | 生命40 / 武力12 / 敏捷6 / 运势8 | — | 锻造 | 3000 |
| `equip_qingmangjian` | 青芒剑 | 绿 | 15 | 武力16 / 防御4 / 敏捷5 | — | 锻造 | 800 |
| `equip_tietaigong` | 铁胎弓 | 绿 | 15 | 武力12 / 防御4 / 敏捷9 | — | 锻造 | 800 |
| `equip_yemankandao` | 野蛮砍刀 | 绿 | 15 | 武力40 | yeman_set | 其他/掉落 | 2000 |
| `equip_pofengji` | 破风戟 | 绿 | 20 | 武力25 / 敏捷5 / 带兵25 | — | 其他/掉落 | 2000 |
| `equip_shushengzhesan` | 书生折扇 | 绿 | 20 | 武力5 / 智力25 / 运势10 | — | 其他/掉落 | 2000 |
| `equip_tiegugun` | 铁骨棍 | 绿 | 20 | 武力30 / 防御5 | — | 其他/掉落 | 2000 |
| `equip_tongchangdao` | 铜长刀 | 绿 | 20 | 生命80 / 武力22 / 防御4 | — | 锻造 | 800 |
| `equip_tongshanchui` | 铜山锤 | 绿 | 20 | 生命160 / 武力32 / 防御5 | — | 其他/掉落 | 2000 |
| `equip_action_beam_glove` | 动感光波手套 | 蓝 | 25 | 武力40 / 敏捷15 | kasukabe_defense_set | 其他/掉落 | 7000 |
| `equip_chuanyungong` | 穿云弓 | 蓝 | 25 | 武力40 / 敏捷18 | — | 其他/掉落 | 6000 |
| `equip_lieyandao` | 烈焰刀 | 蓝 | 25 | 生命160 / 武力50 | — | 其他/掉落 | 6000 |
| `equip_nichangjian` | 霓裳剑 | 蓝 | 25 | 武力10 / 智力35 / 敏捷15 | nichang_set | 其他/掉落 | 6000 |
| `equip_nohara_pan` | 野原家的平底锅 | 蓝 | 25 | 生命180 / 武力36 / 防御12 | kasukabe_defense_set | 其他/掉落 | 6800 |
| `equip_pozhenqiang` | 破阵枪 | 蓝 | 30 | 生命150 / 武力50 / 带兵40 | — | 其他/掉落 | 6000 |
| `equip_xiaoweichangjian` | 校尉长剑 | 蓝 | 30 | 生命72 / 武力45 / 带兵25 | xiaowei_set | 图纸 | 6000 |
| `equip_xingluoshan` | 星罗扇 | 蓝 | 30 | 智力40 / 运势15 | — | 其他/掉落 | 6000 |
| `equip_duanhunbian` | 断魂鞭 | 蓝 | 35 | 武力35 / 敏捷20 / 运势10 | — | 其他/掉落 | 6000 |
| `equip_hanyuejian` | 寒月剑 | 蓝 | 35 | 武力35 / 防御10 / 敏捷15 | — | 其他/掉落 | 6000 |
| `equip_jiaodoushizhichui` | 角斗士之锤 | 蓝 | 35 | 生命250 / 武力45 / 防御8 | jiaodoushi_set | 其他/掉落 | 6500 |
| `equip_qinglongdao` | 青龙刀 | 蓝 | 35 | 生命300 / 武力30 / 智力25 | sixiang_set | 图纸 | 6000 |
| `equip_xuanbinggong` | 玄冰弓 | 蓝 | 35 | 武力40 / 敏捷22 | — | 其他/掉落 | 6000 |
| `equip_baguadao` | 八卦刀 | 蓝 | 40 | 生命200 / 武力40 / 智力20 / 防御5 | — | 其他/掉落 | 6000 |
| `equip_junzijian` | 君子剑 | 蓝 | 40 | 武力25 / 智力35 / 敏捷15 | — | 其他/掉落 | 6000 |
| `equip_zhenyuechui` | 镇岳锤 | 蓝 | 40 | 生命300 / 武力35 / 防御12 | — | 其他/掉落 | 6000 |
| `equip_bawangqiang` | 霸王枪 | 紫 | 45 | 生命237 / 武力46 / 敏捷12 / 带兵50 | — | 其他/掉落 | 16000 |
| `equip_duoqinghuan` | 多情环 | 紫 | 45 | 生命96 / 武力40 / 智力26 / 运势12 | — | 其他/掉落 | 16000 |
| `equip_pojunji` | 破军戟 | 紫 | 45 | 生命214 / 武力50 / 敏捷12 / 带兵45 | — | 其他/掉落 | 16000 |
| `equip_qixingjian` | 七星剑 | 紫 | 45 | 武力47 / 智力19 / 敏捷16 | — | 其他/掉落 | 16000 |
| `equip_huxianjian` | 狐仙剑 | 紫 | 50 | 武力41 / 智力27 / 运势5 | huxian_set | 图纸 | 16000 |
| `equip_jinshejian` | 金蛇剑 | 紫 | 50 | 武力38 / 敏捷27 / 运势15 | — | 其他/掉落 | 16000 |
| `equip_libiegou` | 离别钩 | 紫 | 50 | 武力55 / 敏捷12 / 运势13 | — | 其他/掉落 | 16000 |
| `equip_shuanghuagong` | 霜华弓 | 紫 | 50 | 武力44 / 敏捷22 / 运势14 | — | 其他/掉落 | 16000 |
| `equip_wuqiaojian` | 乌鞘剑 | 紫 | 50 | 武力59 / 敏捷10 / 运势12 | — | 其他/掉落 | 16000 |
| `equip_xuedao` | 血刀 | 紫 | 50 | 生命170 / 武力63 / 敏捷17 | — | 其他/掉落 | 16000 |
| `equip_biyudao` | 碧玉刀 | 紫 | 55 | 武力56 / 敏捷12 / 运势13 | — | 其他/掉落 | 16000 |
| `equip_duomingfeidao` | 夺命飞刀 | 紫 | 55 | 武力38 / 敏捷26 / 运势16 | — | 其他/掉落 | 16000 |
| `equip_fenglingshan` | 凤翎扇 | 紫 | 55 | 武力15 / 智力48 / 运势20 | fengling_set | 图纸 | 16000 |
| `equip_jinshezui` | 金蛇锥 | 紫 | 55 | 武力39 / 敏捷30 / 运势12 | — | 其他/掉落 | 16000 |
| `equip_qilindao` | 麒麟刀 | 紫 | 55 | 生命211 / 武力68 | qilin_set | 图纸 | 16000 |
| `equip_xiuhuazhen` | 绣花针 | 紫 | 55 | 武力36 / 敏捷32 / 运势11 | — | 其他/掉落 | 16000 |
| `equip_xuanwuchui` | 玄武锤 | 紫 | 55 | 生命300 / 武力52 / 防御12 | xuanwu_set | 图纸 | 16000 |
| `equip_ziweiruanjian` | 紫薇软剑 | 紫 | 55 | 武力38 / 敏捷30 / 运势13 | — | 其他/掉落 | 16000 |
| `equip_jinlun` | 金轮 | 紫 | 60 | 生命280 / 武力68 / 防御8 | — | 其他/掉落 | 16000 |
| `equip_tianjizhang` | 天机杖 | 紫 | 60 | 生命187 / 武力15 / 智力50 / 运势15 | — | 其他/掉落 | 16000 |
| `equip_tulongdao` | 屠龙刀 | 紫 | 60 | 生命271 / 武力68 | — | 其他/掉落 | 16000 |
| `equip_yuanyangdao` | 鸳鸯刀 | 紫 | 60 | 武力49 / 敏捷20 / 运势13 | — | 其他/掉落 | 16000 |
| `equip_yuxiao` | 玉箫 | 紫 | 60 | 生命85 / 武力20 / 智力40 / 运势16 | — | 其他/掉落 | 16000 |
| `equip_panlonggun` | 盘龙棍 | 橙 | 65 | 生命240 / 武力78 / 防御10 / 敏捷4 / 运势6 | — | 其他/掉落 | 52000 |
| `equip_tuwujian` | 屠巫剑 | 橙 | 65 | 生命160 / 武力78 / 智力6 / 防御3 / 敏捷10 / 运势3 | — | 其他/掉落 | 52000 |
| `equip_yitianjian` | 倚天剑 | 橙 | 65 | 生命180 / 武力76 / 智力8 / 防御3 / 敏捷10 / 运势5 | — | 其他/掉落 | 52000 |
| `equip_dagoubang` | 打狗棒 | 橙 | 70 | 生命180 / 武力56 / 防御7 / 敏捷24 / 运势15 | — | 其他/掉落 | 52000 |
| `equip_tianyamingyuedao` | 天涯明月刀 | 橙 | 70 | 生命220 / 武力80 / 防御6 / 敏捷12 | — | 其他/掉落 | 52000 |
| `equip_fangtianhuaji` | 方天画戟 | 橙 | 75 | 生命240 / 武力55 / 防御10 / 敏捷8 / 带兵70 | — | 其他/掉落 | 52000 |
| `equip_longdanqiang` | 龙胆枪 | 橙 | 75 | 生命220 / 武力74 / 防御6 / 敏捷18 | — | 其他/掉落 | 65000 |
| `equip_qiankunshengong` | 乾坤神弓 | 橙 | 75 | 生命120 / 武力58 / 智力7 / 防御5 / 敏捷28 / 运势8 | — | 其他/掉落 | 52000 |
| `equip_xiaolifeidao` | 小李飞刀 | 橙 | 75 | 生命100 / 武力52 / 敏捷41 / 运势18 | — | 其他/掉落 | 52000 |
| `equip_changshengjian` | 长生剑 | 橙 | 80 | 生命220 / 武力48 / 智力34 / 防御10 / 运势15 | — | 其他/掉落 | 52000 |
| `equip_kongquelin` | 孔雀翎 | 橙 | 80 | 生命120 / 武力50 / 智力22 / 敏捷26 / 运势18 | — | 其他/掉落 | 52000 |
| `equip_xuantiechongjian` | 玄铁重剑 | 橙 | 80 | 生命280 / 武力84 / 防御10 / 敏捷1 | — | 其他/掉落 | 52000 |
| `equip_xuanyuanjian` | 轩辕剑 | 橙 | 80 | 生命220 / 武力62 / 智力24 / 防御6 / 运势12 | — | 其他/掉落 | 52000 |
| `equip_yuanyuewandao` | 圆月弯刀 | 橙 | 80 | 生命160 / 武力62 / 防御7 / 敏捷22 / 运势14 | — | 其他/掉落 | 52000 |
| `equip_zhuxianjian` | 诛仙剑 | 橙 | 80 | 生命160 / 武力14 / 智力72 / 防御4 / 敏捷6 / 运势10 | — | 其他/掉落 | 52000 |

### 鞋子（30 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_caoxie` | 草鞋 | 白 | 1 | 生命200 / 防御5 / 敏捷10 | starter_cloth_set | 其他/掉落 | 0 |
| `equip_yangpixue` | 羊皮靴 | 白 | 1 | 生命120 / 武力4 / 防御2 / 敏捷8 | — | 锻造 | 300 |
| `equip_buxie` | 布鞋 | 白 | 5 | 生命80 / 智力4 / 防御4 / 敏捷6 | — | 锻造 | 100 |
| `equip_mabuxue` | 麻布靴 | 白 | 10 | 生命150 / 智力5 / 防御8 / 敏捷6 | — | 其他/掉落 | 200 |
| `equip_gangpianxue` | 钢片靴 | 绿 | 10 | 生命200 / 武力4 / 防御8 / 敏捷10 | — | 锻造 | 800 |
| `equip_hupixue` | 虎皮靴 | 绿 | 10 | 生命200 / 武力8 / 防御5 / 敏捷15 | — | 其他/掉落 | 2000 |
| `equip_yanyuxue` | 燕羽靴 | 绿 | 10 | 生命160 / 武力4 / 防御8 / 敏捷14 | — | 锻造 | 1500 |
| `equip_nichangwuxie` | 霓裳舞鞋 | 绿 | 15 | 生命150 / 智力10 / 防御5 / 敏捷20 | nichang_set | 其他/掉落 | 2400 |
| `equip_tayunxue` | 踏云靴 | 绿 | 15 | 生命100 / 武力5 / 防御5 / 敏捷22 | — | 其他/掉落 | 2200 |
| `equip_zhuyelu` | 竹叶履 | 绿 | 15 | 生命100 / 防御5 / 敏捷20 / 运势8 | — | 其他/掉落 | 2100 |
| `equip_jifengxue` | 疾风靴 | 绿 | 20 | 生命200 / 防御8 / 敏捷22 | — | 其他/掉落 | 2500 |
| `equip_youxiaxue` | 游侠靴 | 绿 | 20 | 生命200 / 武力8 / 防御5 / 敏捷20 | — | 其他/掉落 | 2300 |
| `equip_yingxingxue` | 影行靴 | 蓝 | 25 | 生命200 / 防御14 / 敏捷26 | — | 其他/掉落 | 5600 |
| `equip_zhuifengxue` | 追风靴 | 蓝 | 25 | 生命180 / 防御8 / 敏捷30 | — | 其他/掉落 | 5200 |
| `equip_lingboxie` | 凌波鞋 | 蓝 | 30 | 生命200 / 防御6 / 敏捷34 | — | 其他/掉落 | 6000 |
| `equip_pojunxue` | 破军靴 | 蓝 | 30 | 生命200 / 武力12 / 防御14 / 敏捷20 | — | 其他/掉落 | 5800 |
| `equip_xiaoweichangxue` | 校尉长靴 | 蓝 | 30 | 生命200 / 武力5 / 防御14 / 敏捷20 / 带兵10 | xiaowei_set | 图纸 | 5000 |
| `equip_jiaodoushizhixue` | 角斗士之靴 | 蓝 | 35 | 生命200 / 武力12 / 防御14 / 敏捷24 | jiaodoushi_set | 其他/掉落 | 6000 |
| `equip_qinglongxue` | 青龙靴 | 蓝 | 35 | 生命200 / 武力12 / 智力12 / 防御10 / 敏捷20 | sixiang_set | 图纸 | 5500 |
| `equip_jinlinxue` | 金鳞靴 | 蓝 | 40 | 生命200 / 武力11 / 防御14 / 敏捷26 | — | 其他/掉落 | 6200 |
| `equip_liekongxue` | 裂空靴 | 紫 | 45 | 生命240 / 武力14 / 防御16 / 敏捷36 | — | 其他/掉落 | 16500 |
| `equip_huxianxie` | 狐仙鞋 | 紫 | 50 | 生命240 / 智力18 / 防御10 / 敏捷32 | huxian_set | 图纸 | 14000 |
| `equip_qilinxue` | 麒麟靴 | 紫 | 55 | 生命240 / 武力14 / 防御20 / 敏捷30 | qilin_set | 图纸 | 16000 |
| `equip_tianjilu` | 天机履 | 紫 | 55 | 生命240 / 智力18 / 防御15 / 敏捷36 | — | 其他/掉落 | 15500 |
| `equip_xuanwuxue` | 玄武靴 | 紫 | 55 | 生命240 / 武力7 / 防御20 / 敏捷30 | xuanwu_set | 图纸 | 17000 |
| `equip_zhurixue` | 逐日靴 | 紫 | 60 | 生命240 / 武力6 / 防御18 / 敏捷42 | — | 其他/掉落 | 15000 |
| `equip_tatianxue` | 踏天靴 | 橙 | 70 | 生命220 / 武力30 / 防御8 / 敏捷33 | — | 其他/掉落 | 50000 |
| `equip_chiyouzhanxue` | 蚩尤战靴 | 橙 | 75 | 生命220 / 武力20 / 防御8 / 敏捷35 | — | 其他/掉落 | 50000 |
| `equip_wujixingxue` | 无极行靴 | 橙 | 75 | 生命240 / 智力24 / 防御8 / 敏捷31 | — | 其他/掉落 | 55000 |
| `equip_shenxingxue` | 神行靴 | 橙 | 80 | 生命180 / 武力1 / 防御5 / 敏捷51 | — | 其他/掉落 | 48000 |

### 饰品（52 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_fudai` | 福袋 | 绿 | 10 | 生命100 / 防御5 / 运势16 | — | 其他/掉落 | 2000 |
| `equip_luopan` | 罗盘 | 绿 | 10 | 智力20 / 敏捷5 / 运势5 | — | 其他/掉落 | 2000 |
| `equip_qingtongsuanchou` | 青铜算筹 | 绿 | 10 | 智力20 / 运势15 | — | 其他/掉落 | 2000 |
| `equip_tongling` | 铜铃 | 绿 | 10 | 敏捷10 / 运势16 | — | 其他/掉落 | 2000 |
| `equip_moyuyan` | 墨玉砚 | 绿 | 15 | 生命200 / 智力24 / 防御5 | — | 其他/掉落 | 2000 |
| `equip_taomujian` | 桃木剑 | 绿 | 15 | 智力15 / 防御10 / 运势5 | — | 其他/掉落 | 2000 |
| `equip_tongjing` | 铜镜 | 绿 | 15 | 生命250 / 智力5 / 防御14 | — | 其他/掉落 | 2000 |
| `equip_yupei` | 玉佩 | 绿 | 15 | 生命50 / 带兵60 | — | 其他/掉落 | 2000 |
| `equip_zhujian` | 竹简 | 绿 | 15 | 智力24 / 防御5 | — | 其他/掉落 | 2000 |
| `equip_hushenfu` | 护身符 | 绿 | 20 | 生命100 / 武力5 / 防御14 / 运势5 | — | 其他/掉落 | 2000 |
| `equip_tanmunianzhu` | 檀木念珠 | 绿 | 20 | 武力5 / 智力15 / 防御5 / 运势10 / 带兵15 | — | 其他/掉落 | 2000 |
| `equip_taomupai` | 桃木牌 | 绿 | 20 | 智力5 / 防御14 / 运势10 | — | 其他/掉落 | 2000 |
| `equip_tiehufu` | 铁护符 | 绿 | 20 | 生命200 / 防御14 | — | 其他/掉落 | 2000 |
| `equip_yiqiyaopai` | 义气腰牌 | 绿 | 20 | 生命100 / 武力20 / 防御5 / 带兵20 | — | 其他/掉落 | 2000 |
| `equip_fengyuzan` | 凤羽簪 | 蓝 | 25 | 生命100 / 智力15 / 运势20 | — | 其他/掉落 | 6000 |
| `equip_huyaxianglian` | 虎牙项链 | 蓝 | 25 | 生命250 / 武力20 / 敏捷15 | — | 其他/掉落 | 6000 |
| `equip_shiro_leash` | 小白的散步绳 | 蓝 | 25 | 生命300 / 武力5 / 防御14 / 运势10 | kasukabe_defense_set | 其他/掉落 | 5600 |
| `equip_xueyuzhuo` | 血玉镯 | 蓝 | 25 | 生命200 / 武力20 / 敏捷15 | — | 其他/掉落 | 6000 |
| `equip_linghuzhui` | 灵狐坠 | 蓝 | 30 | 生命150 / 智力25 / 敏捷15 | — | 其他/掉落 | 6000 |
| `equip_lingwenyubi` | 灵纹玉笔 | 蓝 | 30 | 生命200 / 智力25 / 运势15 | — | 其他/掉落 | 6000 |
| `equip_liuyunpei` | 流云佩 | 蓝 | 30 | 生命150 / 智力9 / 敏捷15 / 运势15 | — | 其他/掉落 | 6000 |
| `equip_longwenyupei` | 龙纹玉佩 | 蓝 | 30 | 生命100 / 武力12 / 运势15 / 带兵60 | — | 其他/掉落 | 6000 |
| `equip_xingshashouchuan` | 星砂手串 | 蓝 | 30 | 生命150 / 智力16 / 运势20 | — | 其他/掉落 | 6000 |
| `equip_guanxingdeng` | 观星灯 | 蓝 | 35 | 生命100 / 智力10 / 运势22 | — | 其他/掉落 | 6000 |
| `equip_zhaoyaojing` | 照妖镜 | 蓝 | 35 | 生命300 / 武力22 / 运势15 | — | 其他/掉落 | 6000 |
| `equip_qiankunquan` | 乾坤圈 | 蓝 | 40 | 武力22 / 防御10 / 敏捷12 | — | 其他/掉落 | 6000 |
| `equip_tianjiluopan` | 天机罗盘 | 蓝 | 40 | 智力25 / 敏捷5 / 运势20 | — | 其他/掉落 | 6000 |
| `equip_xuantiejie` | 玄铁戒 | 蓝 | 40 | 生命300 / 武力15 / 防御14 | — | 其他/掉落 | 6000 |
| `equip_guiyuanzhu` | 归元珠 | 紫 | 45 | 生命297 / 武力4 / 智力23 / 运势24 | — | 其他/掉落 | 16000 |
| `equip_longjingzhu` | 龙睛珠 | 紫 | 45 | 生命229 / 武力19 / 敏捷16 / 运势18 | — | 其他/掉落 | 16000 |
| `equip_tianjiyupei` | 天机玉佩 | 紫 | 45 | 生命184 / 武力7 / 智力26 / 运势22 | — | 其他/掉落 | 16000 |
| `equip_tianyinguqin` | 天音古琴 | 紫 | 45 | 生命300 / 智力30 / 运势17 | — | 其他/掉落 | 16000 |
| `equip_baguajing` | 八卦镜 | 紫 | 50 | 生命400 / 智力30 / 运势24 | — | 其他/掉落 | 16000 |
| `equip_shenghuoling` | 圣火令 | 紫 | 50 | 生命261 / 武力14 / 智力17 / 运势22 / 带兵80 | — | 其他/掉落 | 16000 |
| `equip_fenghuangyu` | 凤凰羽 | 紫 | 55 | 生命87 / 智力29 / 防御10 / 运势19 | fengling_set | 图纸 | 16000 |
| `equip_pojunhufu` | 破军虎符 | 紫 | 55 | 生命267 / 武力26 / 智力9 / 敏捷16 / 带兵80 | — | 其他/掉落 | 16000 |
| `equip_qilinzui` | 麒麟坠 | 紫 | 55 | 生命235 / 武力24 / 智力6 / 运势24 | qilin_set | 其他/掉落 | 16000 |
| `equip_xuanmingdeng` | 玄冥灯 | 紫 | 55 | 生命256 / 智力30 / 防御13 / 运势4 | — | 其他/掉落 | 16000 |
| `equip_zhuyuye` | 茱萸叶 | 紫 | 55 | 生命320 / 武力21 / 防御14 / 运势22 | — | 其他/掉落 | 0 |
| `equip_zijinhulu` | 紫金葫芦 | 紫 | 55 | 生命225 / 智力30 / 防御18 | — | 其他/掉落 | 16000 |
| `equip_hunyuansan` | 混元伞 | 紫 | 60 | 生命400 / 武力26 / 防御14 / 运势5 | — | 其他/掉落 | 16000 |
| `equip_xuanwuyufu` | 玄武玉符 | 紫 | 60 | 生命286 / 武力16 / 智力10 / 防御18 | — | 其他/掉落 | 16000 |
| `equip_businiaoyu` | 不死鸟羽 | 橙 | 65 | 生命315 / 武力34 / 敏捷14 | — | 其他/掉落 | 52000 |
| `equip_heshibi` | 和氏璧 | 橙 | 65 | 生命290 / 智力38 / 防御1 / 运势25 | — | 其他/掉落 | 52000 |
| `equip_taiqingyuce` | 太清玉册 | 橙 | 65 | 生命200 / 智力38 / 运势16 | — | 其他/掉落 | 52000 |
| `equip_fengshenbang` | 封神榜 | 橙 | 70 | 生命350 / 智力39 / 运势20 | — | 其他/掉落 | 52000 |
| `equip_qiankunzhu` | 乾坤珠 | 橙 | 70 | 生命271 / 武力15 / 智力10 / 防御20 / 敏捷15 / 运势10 | — | 其他/掉落 | 52000 |
| `equip_shangshanfaeling` | 赏善罚恶令 | 橙 | 70 | 生命273 / 武力21 / 智力18 / 防御7 / 运势22 / 带兵60 | — | 其他/掉落 | 52000 |
| `equip_jianghuling` | 江湖令 | 橙 | 75 | 生命350 / 武力20 / 智力20 / 敏捷20 / 带兵80 | — | 其他/掉落 | 52000 |
| `equip_shanheshejitu` | 山河社稷图 | 橙 | 75 | 生命350 / 智力34 / 敏捷15 / 运势10 / 带兵60 | — | 其他/掉落 | 52000 |
| `equip_zhoutianxingpan` | 周天星盘 | 橙 | 75 | 防御20 / 敏捷10 / 运势25 | — | 其他/掉落 | 52000 |
| `equip_chuanguoyuxi` | 传国玉玺 | 橙 | 80 | 生命350 / 武力16 / 智力16 / 防御5 / 运势15 / 带兵160 | — | 其他/掉落 | 52000 |

### 坐骑（29 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_zaohongma` | 枣红马 | 白 | 1 | 生命40 / 武力4 / 防御4 / 敏捷9 | — | 商店 | 620 |
| `equip_xiaomaolv` | 小毛驴 | 白 | 5 | 生命80 / 防御5 / 敏捷10 / 运势5 | — | 其他/掉落 | 600 |
| `equip_huangbiaoma` | 黄膘马 | 白 | 10 | 生命160 / 防御8 / 敏捷12 | — | 商店 | 975 |
| `equip_qingcongma` | 青骢马 | 白 | 10 | 生命150 / 武力5 / 防御5 / 敏捷12 | — | 其他/掉落 | 800 |
| `equip_chibiao` | 赤骠 | 绿 | 10 | 生命180 / 武力8 / 防御5 / 敏捷20 | — | 其他/掉落 | 2000 |
| `equip_dawanma` | 大宛马 | 绿 | 10 | 生命160 / 防御4 / 敏捷16 | — | 商店 | 1500 |
| `equip_taxuema` | 踏雪马 | 绿 | 10 | 防御5 / 敏捷26 | — | 其他/掉落 | 2000 |
| `equip_mengguma` | 蒙古马 | 绿 | 15 | 生命180 / 武力5 / 防御8 / 敏捷20 | — | 其他/掉落 | 2000 |
| `equip_qingzongma` | 青鬃马 | 绿 | 20 | 生命180 / 防御8 / 敏捷25 | — | 其他/掉落 | 2000 |
| `equip_xiliangma` | 西凉马 | 绿 | 20 | 生命180 / 武力8 / 防御5 / 敏捷25 | — | 其他/掉落 | 2000 |
| `equip_feiyunzui` | 飞云锥 | 蓝 | 25 | 生命220 / 防御8 / 敏捷37 | — | 其他/掉落 | 6000 |
| `equip_qianliju` | 千里驹 | 蓝 | 25 | 生命220 / 防御10 / 敏捷30 | — | 其他/掉落 | 6000 |
| `equip_juanmaochitu` | 卷毛赤兔 | 蓝 | 30 | 生命220 / 武力12 / 防御10 / 敏捷23 | — | 其他/掉落 | 6000 |
| `equip_bailongju` | 白龙驹 | 蓝 | 35 | 生命220 / 防御16 / 敏捷19 / 运势12 | — | 其他/掉落 | 6000 |
| `equip_zhaoyeyushizi` | 照夜玉狮子 | 蓝 | 40 | 生命220 / 武力10 / 防御10 / 敏捷30 | — | 其他/掉落 | 6000 |
| `equip_zhuidian` | 追电 | 蓝 | 40 | 生命220 / 防御5 / 敏捷35 / 运势10 | — | 其他/掉落 | 6000 |
| `equip_jueying` | 绝影 | 紫 | 45 | 生命260 / 防御7 / 敏捷41 / 运势15 | — | 其他/掉落 | 16000 |
| `equip_wuzhuima` | 乌骓马 | 紫 | 45 | 生命260 / 武力26 / 防御16 / 敏捷22 | — | 其他/掉落 | 16000 |
| `equip_zhaohuangfeidian` | 爪黄飞电 | 紫 | 50 | 生命260 / 武力10 / 防御10 / 敏捷44 | — | 其他/掉落 | 16000 |
| `equip_qingshuanglu` | 青霜鹿 | 紫 | 55 | 生命260 / 防御18 / 敏捷30 / 运势15 | — | 其他/掉落 | 16000 |
| `equip_dilu` | 的卢 | 紫 | 60 | 生命260 / 防御22 / 敏捷26 / 运势14 | — | 其他/掉落 | 16000 |
| `equip_xuanjiazhanju` | 玄甲战驹 | 紫 | 60 | 生命260 / 武力16 / 防御13 / 敏捷35 / 带兵30 | — | 其他/掉落 | 16000 |
| `equip_qilin` | 麒麟 | 橙 | 65 | 生命300 / 防御24 / 敏捷10 / 运势25 | — | 其他/掉落 | 52000 |
| `equip_hanxuebaoma` | 汗血宝马 | 橙 | 70 | 生命300 / 防御2 / 敏捷48 | — | 其他/掉落 | 52000 |
| `equip_qingluan` | 青鸾 | 橙 | 75 | 生命300 / 智力20 / 防御14 / 敏捷36 | — | 其他/掉落 | 52000 |
| `equip_chituma` | 赤兔马 | 橙 | 80 | 生命300 / 武力34 / 防御14 / 敏捷22 / 带兵40 | — | 其他/掉落 | 52000 |
| `equip_jinjingbailongju` | 金睛白龙驹 | 橙 | 80 | 生命260 / 防御20 / 敏捷34 / 运势24 / 带兵40 | — | 其他/掉落 | 65000 |
| `equip_shandianqiulongju` | 闪电虬龙驹 | 橙 | 80 | 生命260 / 武力16 / 防御6 / 敏捷48 / 运势12 | — | 其他/掉落 | 65000 |
| `equip_shendiao` | 神雕 | 橙 | 80 | 生命300 / 防御10 / 敏捷40 / 运势14 | — | 其他/掉落 | 52000 |

### 器械（17 件）

| key | 名称 | 稀有度 | 需求等级 | 属性 | 套装 | 来源 | 回收价 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `equip_tongchanjiguan` | 铜蝉机关 | 绿 | 10 | 生命200 / 武力12 / 智力5 / 敏捷12 / 全兵种攻击+0.5% | — | 图纸 | 2000 |
| `equip_xiaoxingjiguanshu` | 小型机关鼠 | 绿 | 15 | 生命400 / 武力12 / 敏捷12 / 全兵种生命+0.5% | — | 图纸 | 2000 |
| `equip_qingtongzhijiaodoushi` | 青铜之角斗士 | 绿 | 20 | 生命300 / 武力12 / 防御8 / 敏捷8 / 拳系攻击+0.5% | — | 其他/掉落 | 2000 |
| `equip_qingji` | 庆忌 | 蓝 | 25 | 敏捷16 / 运势8 / 剑系攻击+1% | — | 图纸 | 6000 |
| `equip_jiguanchuniao` | 机关雏鸟 | 蓝 | 30 | 生命400 / 武力15 / 敏捷16 / 弓系攻击+1% | — | 图纸 | 6000 |
| `equip_jixiemao` | 机械猫 | 蓝 | 30 | 生命400 / 武力18 / 智力10 / 敏捷10 / 弓系生命+1% | — | 图纸 | 6000 |
| `equip_baiyinzhijiaodoushi` | 白银之角斗士 | 蓝 | 40 | 生命400 / 武力18 / 防御10 / 敏捷12 / 拳系攻击+1% | — | 其他/掉落 | 6000 |
| `equip_kuileimuren` | 傀儡木人 | 蓝 | 40 | 生命150 / 武力18 / 智力18 / 防御5 / 枪系生命+1% | — | 图纸 | 6000 |
| `equip_kuileiren` | 傀儡人 | 紫 | 45 | 生命179 / 智力24 / 防御10 / 敏捷12 / 剑系全部属性+0.5% | — | 图纸 | 16000 |
| `equip_muniuliuma` | 木牛流马 | 紫 | 45 | 生命300 / 智力24 / 敏捷12 / 运势10 / 全兵种生命+0.5% | — | 图纸 | 16000 |
| `equip_jiguanxiong` | 机关熊 | 紫 | 50 | 生命450 / 武力24 / 防御15 / 拳系攻击+1% | — | 图纸 | 16000 |
| `equip_taotieding` | 饕餮鼎 | 紫 | 55 | 生命450 / 武力18 / 智力24 / 防御15 / 运势10 / 全兵种全部属性+0.25% | — | 图纸 | 0 |
| `equip_huangjinzhijiaodoushi` | 黄金之角斗士 | 紫 | 60 | 生命450 / 武力24 / 防御15 / 敏捷12 / 拳系攻击+1%、拳系防御+0.5% | — | 其他/掉落 | 16000 |
| `equip_xuanwujigui` | 玄武机龟 | 紫 | 60 | 生命450 / 防御18 / 运势10 / 枪系全部属性+0.5% | — | 图纸 | 16000 |
| `equip_feiyuan` | 飞鸢 | 橙 | 65 | 生命450 / 智力26 / 敏捷12 / 运势8 / 全兵种攻击+0.5% | — | 图纸 | 52000 |
| `equip_chixiaojifeng` | 赤霄机凤 | 橙 | 75 | 生命300 / 武力26 / 敏捷12 / 运势8 / 弓系攻击+1.5% | — | 图纸 | 52000 |
| `equip_mojiajiguanren` | 墨家机关人 | 橙 | 80 | 生命450 / 武力25 / 智力25 / 防御12 / 敏捷12 / 运势8 / 全兵种全部属性+0.5% | — | 图纸 | 52000 |

## 五、锻造与图纸成本（原配置）

| key | 名称 | 来源 | 所需锻造 | 材料成本 | 时长(秒) |
| --- | --- | --- | ---: | --- | ---: |
| `equip_chixiaojifeng` | 赤霄机凤 | 图纸 | 10 | tong×106、xi×70、tie×56、xuan_tie_essence×4、zitanmu×15、shuiqumu×10、tiemu×20、heiyuanshi×22、jingangmei×22、paozi×9、zaozi×15、gaolu×8、fire_stone×5、air_stone×3 | — |
| `equip_chiyoujia` | 蚩尤甲 | 图纸 | 10 | equip_tianshukaijia×1、equip_tianxuankaijia×1、equip_tianjikaijia×1、equip_yaoguangkaijia×1、equip_tianquankaijia×1、equip_yuhengkaijia×1、equip_kaiyangkaijia×1 | — |
| `equip_feiyuan` | 飞鸢 | 图纸 | 10 | tong×101、xi×68、tie×53、xuan_tie_essence×4、zitanmu×17、shuiqumu×22、tiemu×15、heiyuanshi×12、jingangmei×12、paozi×15、zaozi×9、gaolu×7、air_stone×4、fire_stone×3 | — |
| `equip_fenghuangyu` | 凤凰羽 | 图纸 | 7 | tong×29、xi×17、tie×12、wood_essence×4、copper_essence×4、zitanmu×10、shuiqumu×11、jingangmei×2、paozi×8、zaozi×2、gaolu×1、air_stone×3、water_stone×2 | — |
| `equip_fenglingguan` | 凤翎冠 | 图纸 | 7 | tong×36、xi×22、tie×17、wood_essence×3、copper_essence×4、zitanmu×9、shuiqumu×6、tiemu×3、jingangmei×3、paozi×6、zaozi×3、gaolu×1、air_stone×3 | — |
| `equip_fenglingshan` | 凤翎扇 | 图纸 | 7 | tong×41、xi×24、tie×20、wood_essence×3、copper_essence×4、zitanmu×11、shuiqumu×10、tiemu×3、jingangmei×3、paozi×9、zaozi×3、gaolu×1、air_stone×3、water_stone×2 | — |
| `equip_huxianjian` | 狐仙剑 | 图纸 | 7 | tong×39、xi×24、tie×20、wood_essence×3、copper_essence×3、xuan_tie_essence×2、zitanmu×6、tiemu×6、heiyuanshi×10、jingangmei×8、paozi×3、zaozi×6、gaolu×2、air_stone×2、water_stone×3 | — |
| `equip_huxianpao` | 狐仙袍 | 图纸 | 7 | tong×41、xi×27、tie×20、wood_essence×4、copper_essence×3、xuan_tie_essence×2、zitanmu×11、shuiqumu×12、tiemu×4、jingangmei×4、paozi×8、zaozi×3、gaolu×2、water_stone×3 | — |
| `equip_huxianxie` | 狐仙鞋 | 图纸 | 7 | tong×32、xi×20、tie×15、wood_essence×3、copper_essence×3、xuan_tie_essence×2、zitanmu×6、shuiqumu×12、tiemu×3、jingangmei×2、paozi×8、zaozi×2、gaolu×1、air_stone×3 | — |
| `equip_jiguanchuniao` | 机关雏鸟 | 图纸 | 8 | tong×51、xi×34、tie×24、wood_essence×3、copper_essence×3、xuan_tie_essence×2、zitanmu×4、shuiqumu×8、tiemu×3、heiyuanshi×4、jingangmei×3、paozi×5、zaozi×3、air_stone×2、water_stone×2 | — |
| `equip_jiguanxiong` | 机关熊 | 图纸 | 10 | tong×68、xi×46、tie×36、wood_essence×3、copper_essence×4、xuan_tie_essence×3、zitanmu×4、tiemu×15、heiyuanshi×12、jingangmei×10、paozi×8、zaozi×9、gaolu×3、earth_stone×3、fire_stone×2 | — |
| `equip_jixiemao` | 机械猫 | 图纸 | 8 | tong×48、xi×32、tie×24、wood_essence×3、copper_essence×3、xuan_tie_essence×2、zitanmu×4、tiemu×6、heiyuanshi×6、jingangmei×4、paozi×4、zaozi×4、air_stone×2 | — |
| `equip_kuileimuren` | 傀儡木人 | 图纸 | 8 | tong×53、xi×36、tie×29、wood_essence×3、copper_essence×4、xuan_tie_essence×2、shuiqumu×6、tiemu×10、heiyuanshi×3、jingangmei×4、paozi×6、zaozi×4、earth_stone×3 | — |
| `equip_kuileiren` | 傀儡人 | 图纸 | 10 | tong×70、xi×46、tie×36、wood_essence×3、copper_essence×4、xuan_tie_essence×3、zitanmu×6、shuiqumu×5、tiemu×16、heiyuanshi×12、jingangmei×9、paozi×9、zaozi×11、gaolu×3、earth_stone×3、air_stone×2 | — |
| `equip_mojiajiguanren` | 墨家机关人 | 图纸 | 10 | tong×116、xi×77、tie×63、xuan_tie_essence×5、zitanmu×15、shuiqumu×10、tiemu×27、heiyuanshi×29、jingangmei×27、paozi×12、zaozi×20、gaolu×10、earth_stone×3、fire_stone×3、water_stone×3 | — |
| `equip_muniuliuma` | 木牛流马 | 图纸 | 10 | tong×65、xi×44、tie×34、wood_essence×4、copper_essence×4、xuan_tie_essence×3、zitanmu×8、shuiqumu×12、tiemu×17、heiyuanshi×5、jingangmei×8、paozi×11、zaozi×8、gaolu×3、earth_stone×3、water_stone×2 | — |
| `equip_qilindao` | 麒麟刀 | 图纸 | 9 | tong×51、xi×32、tie×24、xuan_tie_essence×3、tiemu×10、heiyuanshi×12、jingangmei×9、paozi×3、zaozi×8、gaolu×2、fire_stone×4 | — |
| `equip_qilinjia` | 麒麟甲 | 图纸 | 9 | tong×56、xi×36、tie×29、xuan_tie_essence×3、tiemu×12、heiyuanshi×15、jingangmei×10、paozi×3、zaozi×10、gaolu×3、fire_stone×3、earth_stone×3 | — |
| `equip_qilinkui` | 麒麟盔 | 图纸 | 9 | tong×46、xi×29、tie×22、xuan_tie_essence×2、zitanmu×4、tiemu×8、heiyuanshi×9、jingangmei×6、zaozi×6、gaolu×2、fire_stone×3 | — |
| `equip_qilinxue` | 麒麟靴 | 图纸 | 9 | tong×36、xi×22、tie×17、xuan_tie_essence×2、shuiqumu×10、tiemu×6、jingangmei×4、paozi×6、zaozi×3、gaolu×1、fire_stone×2、air_stone×2 | — |
| `equip_qingji` | 庆忌 | 图纸 | 8 | tong×46、xi×29、tie×22、wood_essence×3、copper_essence×3、xuan_tie_essence×2、zitanmu×6、tiemu×3、heiyuanshi×6、jingangmei×4、paozi×3、zaozi×5、air_stone×2 | — |
| `equip_qinglongdao` | 青龙刀 | 图纸 | 5 | tong×32、xi×20、tie×15、wood_essence×3、copper_essence×3、zitanmu×4、tiemu×5、heiyuanshi×6、jingangmei×5、zaozi×4、air_stone×2、water_stone×2 | — |
| `equip_qinglongjia` | 青龙甲 | 图纸 | 5 | tong×36、xi×22、tie×17、wood_essence×3、copper_essence×3、zitanmu×4、tiemu×6、heiyuanshi×6、jingangmei×4、zaozi×5、water_stone×2、earth_stone×2 | — |
| `equip_qinglongkui` | 青龙盔 | 图纸 | 5 | tong×29、xi×17、tie×12、wood_essence×3、copper_essence×2、zitanmu×5、heiyuanshi×4、jingangmei×3、paozi×3、zaozi×3、air_stone×2 | — |
| `equip_qinglongxue` | 青龙靴 | 图纸 | 5 | tong×22、xi×12、tie×10、wood_essence×3、copper_essence×2、zitanmu×4、shuiqumu×9、paozi×5、air_stone×2 | — |
| `equip_taotieding` | 饕餮鼎 | 图纸 | 10 | tong×72、xi×48、tie×39、wood_essence×3、copper_essence×4、xuan_tie_essence×3、tiemu×12、heiyuanshi×18、jingangmei×15、paozi×3、zaozi×12、gaolu×4、water_stone×2、earth_stone×3、fire_stone×3 | — |
| `equip_tongchanjiguan` | 铜蝉机关 | 图纸 | 6 | tong×36、xi×24、tie×20、wood_essence×2、copper_essence×3、zitanmu×3、shuiqumu×5、heiyuanshi×3、jingangmei×2、paozi×3、zaozi×4、water_stone×2 | — |
| `equip_xiaoweichangjian` | 校尉长剑 | 图纸 | 5 | tong×32、xi×20、tie×15、wood_essence×2、copper_essence×3、tiemu×5、heiyuanshi×8、jingangmei×5、zaozi×5、fire_stone×2 | — |
| `equip_xiaoweichangxue` | 校尉长靴 | 图纸 | 5 | tong×22、xi×12、tie×10、wood_essence×2、copper_essence×2、shuiqumu×8、tiemu×4、paozi×5、air_stone×2 | — |
| `equip_xiaoweikaijia` | 校尉铠甲 | 图纸 | 5 | tong×36、xi×22、tie×17、wood_essence×2、copper_essence×3、tiemu×6、heiyuanshi×9、jingangmei×6、zaozi×6、earth_stone×3 | — |
| `equip_xiaoweitoukui` | 校尉头盔 | 图纸 | 5 | tong×27、xi×17、tie×12、wood_essence×2、copper_essence×2、tiemu×4、heiyuanshi×5、jingangmei×4、zaozi×4、earth_stone×2 | — |
| `equip_xiaoxingjiguanshu` | 小型机关鼠 | 图纸 | 6 | tong×34、xi×22、tie×17、wood_essence×3、copper_essence×2、shuiqumu×8、tiemu×3、jingangmei×2、paozi×4、zaozi×3、air_stone×2 | — |
| `equip_xuanwuchui` | 玄武锤 | 图纸 | 9 | tong×53、xi×34、tie×27、xuan_tie_essence×3、tiemu×12、heiyuanshi×15、jingangmei×11、paozi×3、zaozi×10、gaolu×3、water_stone×2、earth_stone×3、fire_stone×2 | — |
| `equip_xuanwujia` | 玄武甲 | 图纸 | 9 | tong×58、xi×39、tie×32、xuan_tie_essence×3、tiemu×15、heiyuanshi×17、jingangmei×11、paozi×3、zaozi×11、gaolu×3、water_stone×3、earth_stone×3 | — |
| `equip_xuanwujigui` | 玄武机龟 | 图纸 | 10 | tong×75、xi×51、tie×41、wood_essence×3、copper_essence×4、xuan_tie_essence×3、zitanmu×3、tiemu×14、heiyuanshi×17、jingangmei×11、paozi×5、zaozi×11、gaolu×4、water_stone×3、earth_stone×3 | — |
| `equip_xuanwukui` | 玄武盔 | 图纸 | 9 | tong×48、xi×32、tie×24、xuan_tie_essence×2、tiemu×9、heiyuanshi×11、jingangmei×6、zaozi×8、gaolu×2、water_stone×3 | — |
| `equip_xuanwuxue` | 玄武靴 | 图纸 | 9 | tong×39、xi×24、tie×20、xuan_tie_essence×2、shuiqumu×8、tiemu×9、heiyuanshi×5、jingangmei×4、paozi×5、zaozi×4、gaolu×1、earth_stone×3 | — |
| `equip_baihongkui` | 白虹盔 | 锻造 | 9 | tie×30 | 360 |
| `equip_baoweiqiang` | 豹尾枪 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_bumao` | 布帽 | 锻造 | 1 | tong×5 | 120 |
| `equip_bupao` | 布袍 | 锻造 | 1 | tong×5 | 120 |
| `equip_buxie` | 布鞋 | 锻造 | 1 | tong×5 | 120 |
| `equip_changbian` | 长鞭 | 锻造 | 1 | tong×5 | 120 |
| `equip_changgong` | 长弓 | 锻造 | 1 | tong×5 | 120 |
| `equip_changjian` | 长剑 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_changqiang` | 长枪 | 锻造 | 1 | tong×5 | 120 |
| `equip_dakandao` | 大砍刀 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_duandao` | 短刀 | 锻造 | 1 | tong×5 | 120 |
| `equip_duanjian` | 短剑 | 锻造 | 1 | tong×5 | 120 |
| `equip_duanmajian` | 断马剑 | 锻造 | 7 | tie×20 | 300 |
| `equip_fanqugong` | 反曲弓 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_gangpianxue` | 钢片靴 | 锻造 | 5 | tie×10 | 240 |
| `equip_housipao` | 厚丝袍 | 锻造 | 5 | tie×10 | 240 |
| `equip_hutoumao` | 虎头矛 | 锻造 | 5 | tie×10 | 240 |
| `equip_jicibian` | 棘刺鞭 | 锻造 | 5 | tie×10 | 240 |
| `equip_jingtiedao` | 精铁刀 | 锻造 | 7 | tie×20 | 300 |
| `equip_jiulonggangbian` | 九龙钢鞭 | 锻造 | 7 | tie×20 | 300 |
| `equip_mingshejiebian` | 冥蛇节鞭 | 锻造 | 9 | tie×30 | 360 |
| `equip_niupibian` | 牛皮鞭 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_niupimao` | 牛皮帽 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_pansheqiang` | 盘蛇枪 | 锻造 | 7 | tie×20 | 300 |
| `equip_qingmangjian` | 青芒剑 | 锻造 | 5 | tie×10 | 240 |
| `equip_shapijia` | 鲨皮甲 | 锻造 | 7 | tie×20 | 300 |
| `equip_shenbigong` | 神臂弓 | 锻造 | 7 | tie×20 | 300 |
| `equip_shengpijia` | 生皮甲 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_tietaigong` | 铁胎弓 | 锻造 | 5 | tie×10 | 240 |
| `equip_tieyekui` | 铁叶盔 | 锻造 | 5 | tie×10 | 240 |
| `equip_tongchangdao` | 铜长刀 | 锻造 | 5 | tie×10 | 240 |
| `equip_yangpixue` | 羊皮靴 | 锻造 | 3 | tong×10、xi×5 | 180 |
| `equip_yanyuxue` | 燕羽靴 | 锻造 | 7 | tie×20 | 300 |
| `equip_yulindin` | 鱼鳞顶 | 锻造 | 7 | tie×20 | 300 |

---

生成脚本：`/tmp/equip_audit/gen_baseline.py`；原始解析数据：`/tmp/equip_audit/inventory.json`。
