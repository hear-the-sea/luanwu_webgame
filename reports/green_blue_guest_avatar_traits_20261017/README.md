> 后续按用户偏好补用了全部 177 张通用图；当前结果见 [图库补齐记录](../green_blue_avatar_full_coverage_20261017/README.md)。本文件保留此前按人物特点匹配的历史记录。

# 绿蓝门客按人物特点补配通用头像（2026-10-17）

此版本替代此前仅按文武、性别均摊的版本。按用户最新要求，补配之前已配置头像的角色不重新分配。

## 最终范围

- 绿色、蓝色共 3,130 个模板。
- 原本已设置头像的 23 个模板已恢复并保留原图；依据为首轮补配前的 `../green_blue_guest_avatars_20261017/manifest.json` 中的 `old_avatar`，不以本次生成的头像作为“原有头像”。
- 只为原来没有头像的 3,107 个模板补配，共使用 152 张不含人物姓名的通用图片。
- 紫色、橙色、其他品质及所有人物属性不变。当前仅更新本地配置，未同步线上。

## 匹配办法

1. 人工查看并标注 177 张通用图片的可见服饰、道具、身份特点，并区分传统、现代装束；见 `image_traits.json`。
2. 从门客现有生平中提取身份、职业、惯用兵器、书画／音律等特点，开头身份描述优先于后文经历。
3. 从描述中去掉小说书名，避免把全部作品相关角色都当成射手；也避免把受害经历或亲属身份当成本人职业。部分典型歧义根据实际配置生平做显式校正。
4. 优先匹配医药、僧道、书画、琴箫、弓枪刀剑、文官等可见特征；图片文件名的文武标签仅作辅助。比如文类高僧可使用武类目录中真实持佛珠的图片。
5. 在接近最佳匹配的候选中平衡使用数量；同名、同一人物特点的多模板优先保持同一形象。不会为凑次数而改配职业不符的图片。
6. 为本次新补头像的角色排除现代服装和器械；原图保留的角色按用户要求不作覆盖。

匹配基于当前配置文字和有限通用素材，并非逐人的历史肖像考证。通用素材不足的职业保留最接近的古装气质或兵器形象，下方列出自动匹配的首要标签未被素材直接覆盖的条目。

## 典型对应

| 门客 | 对应图片特点 |
|---|---|
| 平一指 | 药葫芦 |
| 胡青牛、薛慕华 | 药草 |
| 程灵素 | 女性古装、手持药草 |
| 鉴真、一行 | 僧衣、佛珠、光头 |
| 丘处机、公孙胜 | 道袍、白须、手杖 |
| 虞世南 | 毛笔、书写案 |
| 成不忧、楚昭南 | 持剑 |
| 王伯当、花荣 | 弓箭 |
| 李应 | 枪 |
| 荀攸 | 谋士扇 |
| 吴三桂、玉兔、吴刚、蒲巴乙、爱德华 | 保留原有形象 |

## 保留原图

| 门客 | 模板 | 原头像 |
|---|---|---|
| 吴三桂 | `hist_mingqing_0054` | `wusangui.png` |
| 蒲巴乙 | `pubayi_green` | `pubayi.png` |
| 马文才 | `orig_ma_wencai` | `monkey_wen_male8.png` |
| 玉兔 | `myth_moon_jade_rabbit` | `yutu.png` |
| 蒲巴乙 | `pubayi_blue` | `pubayi.png` |
| 爱德华 | `orig_edward_blue` | `aidehua.png` |
| 吴刚 | `myth_moon_wu_gang` | `wugang.png` |
| 邢道荣 | `hist_sljnbc_0589_blue` | `pig_wu_male4.png` |
| 潘凤 | `hist_sljnbc_0590_blue` | `pig_wu_male4.png` |
| 邢道荣 | `hist_sljnbc_0589` | `pig_wu_male4.png` |
| 潘凤 | `hist_sljnbc_0590` | `pig_wu_male4.png` |
| 贱王之王 | `task_huashan_jianwang` | `monkey_wu_male7.png` |
| 顶级贱精 | `task_huashan_jianjing` | `monkey_mission.png` |
| 观众甲 | `task_huashan_audience_a` | `buyi.png` |
| 猛虎 | `task_jingyang_tiger` | `menghu.png` |
| 武师教头 | `task_fugui_instructor` | `monkey_wu_male10.png` |
| 机关熊 | `task_matchmaking_bear` | `equip_jiguanxiong.png` |
| 钻山豹 | `task_wulong_bandit_chief_zuanshanbao` | `monkey_mission.png` |
| 座山雕 | `task_wulong_bandit_chief_zuoshandiao` | `monkey_mission.png` |
| 改装机械猫 | `task_mechanism_cat` | `equip_jixiemao.png` |
| 黑衣人 | `task_mechanism_bird` | `monkey_mistry.png` |
| 土匪 | `task_nine_peaks_bandit` | `monkey_mission.png` |
| 乌鳞寨喽啰 | `guild_wulinzhai_minion` | `monkey_mission.png` |

## 素材不足时的近似匹配

例如当前通用图库缺少女将盔甲、女性道袍、女性长刀等专门图片；保留性别与古装风格，尽量选择带武器、书卷等相近形象。

| 门客 | 生平首要特点 | 选择的通用图片 |
|---|---|---|
| 第二梦 | 刀 | `monkey/其他随机角色/武083_女_.png` |
| 薛可人 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 孙秀青 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 公孙兰 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 华真真 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 明月心 | 谋士 | `monkey/其他随机角色/文084_女.png` |
| 青青 | 刀 | `monkey/其他随机角色/武083_女_.png` |
| 高亚男 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 巴寡妇清 | 理财商贸 | `monkey/其他随机角色/文064_女.png` |
| 孙尚香 | 将领／军旅 | `monkey/其他随机角色/武083_女_.png` |
| 花木兰 | 将领／军旅 | `monkey/其他随机角色/武083_女_.png` |
| 萧绰 | 官员 | `monkey_wen_female7.png` |
| 管道升 | 书画 | `monkey/其他随机角色/文084_女.png` |
| 鱼玄机 | 道士／道术 | `monkey/其他随机角色/文064_女.png` |
| 妇好 | 将领／军旅 | `monkey/其他随机角色/武083_女_.png` |
| 卓氏 | 理财商贸 | `monkey/其他随机角色/文064_女.png` |
| 姬宫涅 | 君王 | `monkey_wen_female7.png` |
| 女魃 | 将领／军旅 | `monkey/其他随机角色/武083_女_.png` |
| 沈落雁 | 谋士 | `monkey/其他随机角色/文064_女.png` |
| 傅君嫱 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 程瑶迦 | 道士／道术 | `monkey_wen_female10.png` |
| 澄静 | 僧人／佛门 | `monkey_wen_female5.png` |
| 韩小莹 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 闵柔 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 秦红棉 | 刀 | `monkey/其他随机角色/武083_女_.png` |
| 关明梅 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 史小翠 | 刀 | `monkey/其他随机角色/武083_女_.png` |
| 程英 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 萧中慧 | 刀 | `monkey/其他随机角色/武083_女_.png` |
| 花无媸 | 天文历算 | `monkey/其他随机角色/文084_女.png` |
| 刘郁芳 | 剑 | `monkey/其他随机角色/武083_女_.png` |
| 扈三娘 | 刀 | `monkey/其他随机角色/武083_女_.png` |

## 原始资料中的矛盾

- 巴寡妇清的配置性别为男性，而生平明确写“秦代巴郡女商人”。头像按生平选择女性书卷形象，原有游戏性别字段不变。
- 姬宫涅、澄静等模板当前配置为女性，因此仍按该字段选图；身份与性别数据需要后续单独核对，本次不改人物属性。

## 验证

- 对照首次补配前的 YAML 备份，确认只有绿蓝头像字段发生变化。
- 全部 23 个原有头像与首次补配前记录逐一一致。
- 新补的 3,107 个头像全部来自已标注的受控通用图库，无现代装束。
- 27 个典型人物身份回归核对通过，并核对 4 名女性刀客的近似武器图，避免分配成书生。
- 全部 3,130 个模板通过实际导入命令的解析、构建、头像字段及源文件存在性核对。
- `python -m pytest tests/test_load_guest_templates_command.py -q`：28 passed。
- 可运行：`PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python reports/green_blue_guest_avatar_traits_20261017/verify.py`。

## 文件

- [30 个典型人物预览](preview.jpg)。
- `assignments.csv`：完整分配表。
- `manifest.json`：逐人匹配依据、实际选图及候选数量。
- `image_traits.json`：逐图特点标注。
- `assign.py`：本次匹配脚本；`verify.py`：独立核对脚本。
- `before_traits.zip`：本次特点重配之前的 YAML 备份。原始补配之前的配置见 `../green_blue_guest_avatars_20261017/original_rosters.zip`。
