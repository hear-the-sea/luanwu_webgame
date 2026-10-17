# 橙色任务 NPC 简介补充审核稿（2026-10-17）

本文件仅供审核，未修改门客 YAML、数据库或服务。40 条任务专用橙色模板现均没有 `flavor` 简介，且均为 `recruitable: false`。玩家可获得的橙色门客建议优先审核；本文件适合作为任务 NPC 第二批补充。

稿件只依据本地任务剧情与模板，未补入未经核实的原著、历史或自创身世。资料不足时采用保守短稿；“待补设定”属于编辑备注，不写入玩家简介。诛仙残锋、符诏、仙印、法宝、天罚等是战斗实体，不应全部描述成人类门客。

重要一致性：十二仙印关卡明文说明来者并非仙人本尊；虎牢关吕布与白门楼吕布为不同任务模板，不能用一个末路版本覆盖两者。

## 逐条建议

以下每条原文均为：**空白（未配置 `flavor`）**。建议均为新增，不涉及其他属性调整。

### 1. 南宫烈（`task_wanxian_nangong_lie`）

建议稿（82 字）：

> 万仙阵外，碧游残灯将熄，南宫烈出现在封锁阵门的敌阵之中。庄园门客应残魂之请入阵救人，与他相逢于这场清剿与求生的交锋。第一面万仙阵旗尚未夺回，阵中余众的去路仍未打开。

审核备注：保守短稿；仅确认敌阵武者，所属宗门、职衔与身世待补。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:721)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:870)。

### 2. 陆玄清（`task_wanxian_lu_xuanqing`）

建议稿（79 字）：

> 陆玄清现身于万仙阵门，与清剿军、巡阵者一道阻住前来援救的庄园门客。彼时截教败局已现，尚未入榜的残众仍在阵中等待生路，而碧游残灯所唤来的援手，正要穿过这片封锁。

审核备注：保守短稿；仅确认敌阵文系角色，勿擅定其为玉虚真人或某仙弟子。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:733)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:870)。

### 3. 白鹤符使（`task_wanxian_baihe_fushi`）

建议稿（83 字）：

> 白鹤符使出现在碧游残灯照见的阵门敌阵中。万仙阵旗折断，周营与玉虚门人已经封住退路，前来救援的庄园门客必须穿过重重阻隔。此刻一线灯火未灭，阵中残众便仍有等待生还的理由。

审核备注：保守短稿；称号已有，具体受谁差遣、是否骑鹤等均无明文。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:747)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:870)。

### 4. 诛仙残锋（`task_wanxian_zhuxian_canfeng`）

建议稿（85 字）：

> 万仙阵外失控的剑痕尚未平息，诛仙残锋便列在阵门敌阵之中。它与清剿军、巡阵者共同阻住救援之路，使夺回阵旗的行动更添险阻。碧游残灯照着残破阵门，截教余众的生路仍悬于剑影之间。

审核备注：保守短稿；按名称及关卡剑痕语境描述，勿定为通天本尊或完整诛仙剑。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:761)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:870)。

### 5. 余烛（`task_wanxian_yuzhu`）

建议稿（91 字）：

> 余烛出现在万仙阵门的敌阵中，与入阵救援的庄园门客相对。四周阵旗折断，清算符诏正在抹去榜外残名，碧游残灯也只剩一线微光。这场交锋关乎残众能否脱身，尚未入榜者的命运，仍悬在这道阵门之后。

审核备注：保守短稿；名字易被误作截教援手，当前实际配置为敌方，身世待补。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:775)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:870)。

### 6. 清算符诏（`task_wanxian_qingsuan_fuzhao`）

建议稿（86 字）：

> 悬于万仙阵门上方的清算符诏，一笔一笔勾去榜外残名。它没有凡人的面目，却把败局化作不容分说的敕令。庄园门客循碧游残灯而来，要从这道清算之下夺回阵旗，给尚未作恶的残众留出生路。

审核备注：法诏化身，不按普通人物补家世。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:789)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:870)。

### 7. 哪吒（`task_wanxian_nezha`）

建议稿（84 字）：

> 万仙阵中层降下玉虚仙印，哪吒之名亦随之显现。这并非仙人本尊，而是承载其法宝、道统与杀伐意志的印中显化。杀星在前开路，将援助截教的庄园门客视作乱榜异数，阻住残部反攻之路。

审核备注：务必保留“并非本尊”。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:803)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 8. 杨戬（`task_wanxian_yangjian`）

建议稿（83 字）：

> 万仙阵中层，承载杨戬之名的玉虚仙印显现于敌阵。这并非仙人本尊，而是玄功与道统意志的印中显化。十二印将合未合，截教残众的去路再度被封，庄园门客也被卷入天命对异数的裁断。

审核备注：务必保留“并非本尊”；不另加师承、三尖刀等未见本关的设定。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:817)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 9. 太乙（`task_wanxian_taiyi`）

建议稿（85 字）：

> 玉虚仙印落入万仙阵中层，太乙之名随神火一同显现。这并非仙人本尊，而是承载其法宝、道统与杀伐意志的印中显化。神火映照残阵，前来援助截教的庄园门客，已被这道天命视为乱榜异数。

审核备注：务必保留“并非本尊”。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:832)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 10. 广成子（`task_wanxian_guangchengzi`）

建议稿（85 字）：

> 万仙阵中层，广成子之名随着玉虚仙印显现，番天印威势压向截教残存的气运。这并非仙人本尊，而是法宝与道统意志的化现。十二印封锁残阵，碧游余众能否从逃亡转为反攻，仍待此战分晓。

审核备注：务必保留“并非本尊”。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:846)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 11. 赤精子（`task_wanxian_chijingzi`）

建议稿（83 字）：

> 赤精子之名出现在万仙阵中层的玉虚仙印之中。此处降临的并非仙人本尊，而是承载诸仙法宝、道统与杀伐意志的印中显化。他与其余仙印同列敌阵，将援助截教的庄园门客挡在残阵深处。

审核备注：保守短稿；关卡无其单独情节，不扩写阴阳镜等法宝。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:860)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 12. 慈航（`task_wanxian_cihang`）

建议稿（84 字）：

> 慈航之名随十二道玉虚仙印降入万仙阵中层。此处显现的并非仙人本尊，而是道统与法宝意志在阵中的化现。仙印合拢，截教残众便将重回死局；庄园门客由此直面天命对援手与异数的裁断。

审核备注：保守短稿；不混入其他宗教阶段的身份或外貌。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:874)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 13. 惧留孙（`task_wanxian_juliusun`）

建议稿（83 字）：

> 万仙阵中层，惧留孙之名列于降下的玉虚仙印之间。此处来者并非仙人本尊，而是法宝、道统与杀伐意志的印中显化。截教残部才见生路，十二印便再度合围，阻挡庄园门客继续深入残阵。

审核备注：保守短稿；无独立经历，不擅加捆仙绳、弟子关系。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:889)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 14. 十二仙印（`task_wanxian_shier_xianyin`）

建议稿（85 字）：

> 降在万仙阵中层的十二道玉虚仙印，并非诸仙亲临，却承载着他们的法宝、道统与杀伐意志。仙印若合，截教残部将被重新压回死局；唯有打破这重封锁，残阵才会重开，逃亡才可能转为反攻。

审核备注：阵中法印集合，勿写作第十三位仙人。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:903)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:904)。

### 15. 燃灯（`task_wanxian_randeng`）

建议稿（84 字）：

> 封神榜裂开一页，天门显现，燃灯道人在门前点起过去佛灯。灯火照见前尘因果，也照着尚未被清算的截教残众。庄园门客来到此处，已从阵外救援走向夺榜改命，而他正立在这道天命之前。

审核备注：依据任务与佛灯技能叙述，不补封神原著生平。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:917)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:943)。

### 16. 南极仙翁（`task_wanxian_nanji_xianweng`）

建议稿（84 字）：

> 南极仙翁列于封榜天门的敌阵之中。十二仙印已碎，封神榜裂页显门，截教最后的榜外残命悬于一线。庄园门客由救援而来，直面榜上神名与玉虚天罚；这位仙翁的身影，也在天门之前显现。

审核备注：保守短稿；本关仅名单，无独立台词或往事。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:931)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:943)。

### 17. 姜子牙（`task_wanxian_jiang_ziya`）

建议稿（86 字）：

> 封榜天门前，姜子牙持封神榜而立，金光铺开，榜上神名随敕命齐鸣。对截教残众而言，这是清算将至的一刻；对庄园门客而言，救援已成为夺榜改命之战。天命大势仍在，榜外残生却尚未尽灭。

审核备注：采用本关持榜执行封命的身份，区别于历史姜尚模板。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:946)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:943)。

### 18. 打神鞭（`task_wanxian_dashenbian`）

建议稿（83 字）：

> 封榜天门显现时，打神鞭化为天罚，鞭影如天律长痕，直指逆命之魂。它不是寻常随军武者，而是封榜清算之力在阵前的显现。庄园门客救援至此，面对的已是法宝与敕令共同划下的命数。

审核备注：法宝显化，勿硬设成人物。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:961)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:943)。

### 19. 榜上名（`task_wanxian_bangshangming`）

建议稿（84 字）：

> 封神榜金光中，榜上神名逐一醒来，列在封榜天门之前。它们没有在此留下各自的姓名与生平，却共同映出封榜已定的秩序。庄园门客为榜外残众而来，与这些从金光中苏醒的神名迎面相逢。

审核备注：集合性显化；不擅自指定榜上神祇名单。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:975)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:943)。

### 20. 玉虚天罚（`task_wanxian_yuxu_tianfa`）

建议稿（85 字）：

> 封榜天门之前，玉虚敕令引动雷火，天罚随之降下。它所指向的，是试图为截教残众争得生路的逆命之人。封神大势即将落定，庄园门客仍要在这道敕令面前，守住不该被一并抹去的榜外残命。

审核备注：天罚显化，不是独立仙人。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:989)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:943)。

### 21. 春日部巡逻队长（`task_kasukabe_patrol_captain`）

建议稿（85 字）：

> 春日部巡逻队长出现在被神奇蜡笔搅乱的临时游乐战场。防卫队追查失落蜡笔，却让庄园附近陷入涂鸦迷宫与恶作剧的混乱。这位队长也列入了庄园门客必须经过的试炼，挡在回收蜡笔的路上。

审核备注：保守短稿；不指定其为某个原作角色。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1003)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:176)。

### 22. 玩具机关鳄鱼（`task_kasukabe_toy_crocodile`）

建议稿（88 字）：

> 玩具机关鳄鱼出现在春日部游乐战场，与恶作剧陷阱一同构成回收蜡笔的阻碍。神奇蜡笔使庄园四周变得面目全非，连平日熟悉的道路也成了试炼的一部分。这只机关玩具，便是其中难以绕开的身影。

审核备注：保守短稿；不擅加制造者、动力源与原作来历。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1017)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:176)。

### 23. 迷路的防卫队员（`task_kasukabe_defense_member`）

建议稿（89 字）：

> 春日部防卫队为追查被夺走的神奇蜡笔来到庄园，却把附近搅成了游乐战场。一名迷路的防卫队员也出现在这场试炼中，与前来平息混乱的庄园门客相遇。蜡笔未被找回之前，众人的道路仍被涂鸦打乱。

审核备注：保守短稿；不可指认为小新、风间等具名角色。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1031)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:176)。

### 24. 蜡笔恶作剧分身（`task_kasukabe_crayon_clone`）

建议稿（83 字）：

> 蜡笔恶作剧分身现身于春日部游乐战场，与玩具机关、涂鸦迷宫一同拦住回收蜡笔的道路。神奇蜡笔的混乱尚未平息，庄园门客必须穿过这场真假难辨的恶作剧，才能继续追查涂鸦大魔王。

审核备注：保守短稿；未明确是谁的分身，不写“小新的分身”。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1045)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:176)。

### 25. 涂鸦大魔王（`task_kasukabe_doodle_boss`）

建议稿（89 字）：

> 受神奇蜡笔力量影响的涂鸦大魔王，出现在庄园附近的春日部游乐战场。四处涂鸦把粮仓、演武场与巡逻路线搅得面目全非，防卫队的追查也因此失控。庄园门客为平息混乱而来，最终将与它正面相遇。

审核备注：按本关设定描述，不编造真实身份或夺笔者身份。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1059)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:176)。

### 26. 蚩尤（`task_zhuolu_chiyou`）

建议稿（87 字）：

> 封榜天门之后，中原龙脉受封神余威震动，唤起上古涿鹿之战的兵主命格。九黎战旗自地脉升起，蚩尤率风伯、雨师重开战局，再问中原归属。庄园门客循轩辕剑光示警而来，与这位兵主正面逐鹿。

审核备注：明确为本关再起的上古战局，不与历史传说条目混写。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1074)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:979)。

### 27. 风伯（`task_zhuolu_fengbo`）

建议稿（86 字）：

> 九黎战旗自中原地脉升起，风伯随蚩尤重入涿鹿战局。封神余威唤醒了上古兵争，也令中原龙脉再度面临争夺。庄园门客循轩辕剑光而来，所见不仅是旧日战旗，还有随兵主一同再临的风伯身影。

审核备注：仅采用本关九黎阵营，不指定神话谱系、外貌与家世。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1090)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:979)。

### 28. 雨师（`task_zhuolu_yushi`）

建议稿（85 字）：

> 封神余威触动中原龙脉，雨师随蚩尤、风伯重开涿鹿战局。上古兵争从地脉中再现，九黎战旗所向，仍是中原归属。庄园门客奉轩辕剑光示警入阵，在这场再起的逐鹿之争中，与雨师迎面相逢。

审核备注：仅采用本关九黎阵营，不补神话身份争议。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1105)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:979)。

### 29. 食铁兽（`task_zhuolu_shitieshou`）

建议稿（85 字）：

> 食铁兽列于逐鹿中原的九黎敌阵，与蚩尤、风伯、雨师一同迎向庄园门客。中原龙脉受封神余威震动，上古战旗由地脉再起，这些兽影也随战局出现，成为再争中原的兵锋之中格外醒目的存在。

审核备注：保守短稿；不把民间熊猫坐骑说法当作本关既定设定。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1120)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:979)。

### 30. 黑风关守将（`guild_blackwind_gate_general`）

建议稿（83 字）：

> 黑风关守将镇守山口堡寨，身后是亡命之徒与旧军头子修成的层层防线。壕沟新翻，城头轮火不熄，关内粮械齐备。昔日军镇已成险关，他便立在这片仍能彼此接应、轮番调兵的守御之中。

审核备注：依据关卡与称号，姓名、旧军归属待补。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1135)、[关卡剧情](/home/daniel/code/web_game_v5/data/guild_mission_templates.yaml:90)。

### 31. 单于（`task_barbarian_chanyu`）

建议稿（86 字）：

> 北方首领大胡子单于觊觎中原土地已久，率军南下，并强征壮丁在营中服役。蒲巴乙逃营示警后，各地庄主联手设下埋伏，使他的军队由得意进逼转为溃败奔逃。入侵与追击两战，皆可见其身影。

审核备注：本作原创关卡首领，不对应某一历史匈奴单于；去掉旧描述的族群贬义。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1151)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:783)。

### 32. 袁绍（`task_guandu_yuanshao`）

建议稿（83 字）：

> 四世三公之后，坐拥河北四州之众。官渡前线，袁绍挟盛大军势压境，曹军粮道危急，乌巢成为战局转折的关键。庄园门客将于河北军势最盛之时直面其人，在这场相持中争夺最后的胜局。

审核备注：关卡袁绍，避免把文案“号称百万”当可靠史实复述。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1166)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1014)。

### 33. 吕布（`task_hulao_lvbu`）

建议稿（84 字）：

> 虎牢关前，吕布骑赤兔、执方天画戟，单骑出阵，逼得诸侯联军群英应战。汜水关战报刚刚传来，他便以锋芒正盛的姿态站在下一重关口。此时飞将尚未走到末路，眼前仍是群雄逐鹿的战场。

审核备注：虎牢关版本；不写白门楼围困或命运抉择。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1179)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1155)。

### 34. 华雄（`task_sishui_huaxiong`）

建议稿（84 字）：

> 汜水关前，华雄凭勇力压住联军先锋，立于关楼频频邀战，成为西凉军守关的锋头。飞将浮沉录由这一关展开，庄园门客将与他交锋；军帐中尚未传出的战报，则把此地与虎牢关的风云相连。

审核备注：采用本关西凉武将与邀战形象，勿写关羽尚未发生的斩将结局。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1192)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1120)。

### 35. 吕布（`task_baimenlou_lvbu`）

建议稿（84 字）：

> 下邳被曹军包围，白门楼成为吕布最后的落脚处。高顺整军、张辽寻路、陈宫筹谋、臧霸守部，他却仍相信一次冲阵便能改写胜负。暗中流转的招贤帖，为这位飞将的末路留下了另一种可能。

审核备注：白门楼版本；不先断言已死或必被招募。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1208)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1191)。

### 36. 高顺（`task_baimenlou_gaoshun`）

建议稿（82 字）：

> 曹军围住下邳时，高顺仍在白门楼下整顿军伍，与吕布旧部共同守着最后的阵地。飞将尚盼再度冲阵，城内众人也各寻出路。这位武将留在合围之中，见证吕布的末路是否还会出现转机。

审核备注：保守短稿；本关只明确整军，不补“陷阵营”等外部历史资料。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1224)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1191)。

### 37. 张辽（`task_baimenlou_zhangliao`）

建议稿（85 字）：

> 白门楼风声渐紧，张辽在曹军合围之中寻找退路。吕布仍想凭一次冲阵改变局势，高顺整军，陈宫筹谋，而他也与旧部一道置身这场最后的抉择。城楼前的交锋未定，乱世中的去向便尚未落笔。

审核备注：仅写本关寻找退路；不提前写降曹、合肥等后续。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1240)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1191)。

### 38. 陈宫（`task_baimenlou_chengong`）

建议稿（84 字）：

> 下邳被围，白门楼已近末路，陈宫仍在推演一条不必投降的路。吕布盼着再次冲阵，旧部则各自支撑眼前的危局。这位谋士的筹划，连同暗中流转的招贤帖，让飞将的最后一段故事仍存变数。

审核备注：本关谋士；不强加拒降赴死的既定结局。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1256)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1191)。

### 39. 臧霸（`task_baimenlou_zangba`）

建议稿（84 字）：

> 白门楼风声最紧时，臧霸仍守着最后的旧部。曹军已围住下邳，吕布尚信冲阵可以翻盘，众将也在危局中各寻出路。他与吕布、高顺、张辽、陈宫一同列阵，等待这场命运抉择写下最后一笔。

审核备注：保守短稿；本关只明确守旧部，不嫁接历史臧霸其他战功。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1272)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1191)。

### 40. 黄裳（`task_huashan_huang_shang`）

建议稿（87 字）：

> 无名论剑帖传遍江湖后，黄裳现身华山九重关，与扫地僧、张三丰、独孤求败等高手同列峰顶。山巅不凭年龄排座次，也少了客套寒暄；他作为守关者之一，在这场只问手中本领的论剑中等待来客。

审核备注：保守短稿；仅依据本作任务，未查外部原著，不补《九阴真经》成书情节。

依据：[模板定义](/home/daniel/code/web_game_v5/data/guests/special.yaml:1288)、[关卡剧情](/home/daniel/code/web_game_v5/data/mission_templates.yaml:1054)。

## 审核后执行边界

确认后仅补充上述模板的 `flavor` 字段；不改名字、稀有度、属性、技能、招募资格或关卡剧情。资料不足的 4 位阵门人物可先留空，待补充角色设定后再写长稿，避免用通用剧情凑出虚构身世。
