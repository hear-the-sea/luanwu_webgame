# 橙色门客玩家版简介更新（2026-10-17）

按审核意见落地，并进一步改写为直接面向玩家的人物介绍。

- 更新38名常规橙色门客；身份、经历、性格连贯展开，保留适量人物气势。
- 补齐40条任务／关卡角色简介；只使用现有关卡背景，资料较少者保留短稿。
- 刘邦、刘秀、女娲、伏羲、神农5篇原文保留。
- 删除考据纠错旁白、编辑备注、攻略引导与绝对战力排名；需要辨明传说的地方以自然叙述处理。
- YAML共修改11个文件，仅更新橙色模板flavor；所有其他字段逐一核对不变。
- 已同步到隔离本地数据库，仅修改GuestTemplate.flavor；头像及实例易容均不变。
- 生产未更新。

## 玩家版示例

### 白起

（？—前257年）战国秦国名将，封武安君。他长期为秦昭襄王领兵，在伊阙击败韩魏联军，又于鄢郢之战攻破楚国都城，使楚国失去大片西部疆土。长平之战中，他接替秦军主将，利用赵军出击之机切断退路与补给，迫使赵军投降；随后大规模杀俘，也让武安君之名带上沉重血色。他善于把握战场态势，以歼灭敌军有生力量见长，一次次改变列国之间的强弱格局。晚年因是否继续进攻赵国而与秦廷失和，被削爵迁徙，最终在杜邮被赐死。战阵上的凌厉决断，未能为他换来安稳归途。

### 朱棣

（1360年—1424年）明成祖，年号永乐。原封燕王，驻守北平，后发动靖难之役夺取皇位。即位后整顿军政，营建北京宫殿，疏通漕运，于1421年迁都北京，将帝国中枢移向北疆。他五次亲征漠北，任内命郑和六次下西洋，扩大远洋交通与朝贡往来；又组织编纂《永乐大典》，汇聚典籍，保存文献。夺位后的清洗，与远征、营建所耗民力，同样伴随着永乐盛业。1424年，他死于北征归途。朱棣行事强硬，敢作大举，即便年老多病仍亲履行阵，一生雄心仿佛始终不肯停歇。

### 赵匡胤

（927年—976年）宋太祖，北宋开国皇帝。原为后周禁军将领，960年陈桥兵变，黄袍加身，建立宋朝。身处五代武将反复夺权的余波之中，他逐步收归禁军统领、地方财赋与行政权力，削弱藩镇割据，重建朝廷的威望与秩序。对外采取先南后北的方略，先后平定荆湖、后蜀、南汉、南唐，为统一大业奠定基础。他重视文臣治政，也深知将帅领兵的分量，善以权术与制度化解兵权之争。从行伍中走出的天子，并未只靠刀兵守成；收敛锋芒、稳住人心，正是他在乱世尽头开创新朝的本领。

### 吴明

出自古龙《陆小凤》系列《凤舞九天》，人称“小老头”，居于一座隐秘的海外孤岛。他外表和气平凡，常带笑容，言谈间却显露出广博见识与敏锐心机。岛上聚集着一批深藏不露的高手，在他的安排下隐匿身份、暗中行动，以难以追查的手段攫取财富。他深谙享乐，也善于观察人的欲望，往往在客气交谈之间便已衡量好对方的价值。陆小凤意外登岛后，他看中了对方的胆识与本领，试图将其纳入自己的计划。他不急于公开扬名，更喜欢藏在局势背后，静候旁人一步步走进自己铺好的道路。

## 更新清单

|人物|模板|原字数|新字数|
|---|---|---:|---:|
|韦英雄|`hero_ilrock_04576`|138|207|
|燕南天|`hero_yan_nantian`|247|215|
|吴明|`hero_wu_ming`|248|218|
|柴玉关|`hero_kuai_huo_wang`|245|215|
|朱元璋|`hist_mingqing_0001`|283|227|
|朱棣|`hist_mingqing_0002`|264|217|
|刘彻|`hist_qinhan_0002`|218|223|
|项羽|`hist_qinhan_0023`|196|217|
|赵云|`hist_sljnbc_0012`|143|220|
|曹操|`hist_sljnbc_0001`|136|211|
|诸葛亮|`hist_sljnbc_0002`|141|216|
|刘裕|`hist_sljnbc_0004`|146|214|
|拓跋焘|`hist_sljnbc_0005`|138|217|
|刘备|`hist_sljnbc_0006`|146|221|
|关羽|`hist_sljnbc_0007`|149|228|
|吕布|`hist_sljnbc_0013`|153|218|
|赵匡胤|`hist_songliaojinyuan_0001`|120|220|
|铁木真|`hist_songliaojinyuan_0002`|124|226|
|忽必烈|`hist_songliaojinyuan_0003`|124|228|
|李世民|`hist_suitang_0001_lishimin`|156|215|
|杨坚|`hist_suitang_0002_yangjian`|165|219|
|武曌|`hist_suitang_0003_wuzhao`|155|224|
|嬴政|`hist_xianqin_0001`|174|206|
|姬昌|`hist_xianqin_0002`|183|187|
|姬发|`hist_xianqin_0003`|169|195|
|姒禹|`hist_xianqin_0004`|181|196|
|子履|`hist_xianqin_0005`|180|196|
|孔丘|`hist_xianqin_0006`|168|217|
|白起|`hist_xianqin_0031`|169|216|
|孙武|`hist_xianqin_0008`|182|209|
|李耳|`hist_xianqin_0007`|176|199|
|帝尧|`hist_xianqin_0438`|198|187|
|轩辕|`hist_xianqin_0436`|201|191|
|帝舜|`hist_xianqin_0439`|192|198|
|阿青|`hero_a_qing`|189|209|
|石破天|`hero_shi_zhongjian`|198|209|
|扫地僧|`hero_sao_di_seng`|196|216|
|独孤求败|`hero_dugu_qiubai`|204|215|
|南宫烈|`task_wanxian_nangong_lie`|0|76|
|陆玄清|`task_wanxian_lu_xuanqing`|0|75|
|白鹤符使|`task_wanxian_baihe_fushi`|0|75|
|诛仙残锋|`task_wanxian_zhuxian_canfeng`|0|85|
|余烛|`task_wanxian_yuzhu`|0|79|
|清算符诏|`task_wanxian_qingsuan_fuzhao`|0|86|
|哪吒|`task_wanxian_nezha`|0|85|
|杨戬|`task_wanxian_yangjian`|0|85|
|太乙|`task_wanxian_taiyi`|0|90|
|广成子|`task_wanxian_guangchengzi`|0|88|
|赤精子|`task_wanxian_chijingzi`|0|85|
|慈航|`task_wanxian_cihang`|0|80|
|惧留孙|`task_wanxian_juliusun`|0|84|
|十二仙印|`task_wanxian_shier_xianyin`|0|95|
|燃灯|`task_wanxian_randeng`|0|87|
|南极仙翁|`task_wanxian_nanji_xianweng`|0|89|
|姜子牙|`task_wanxian_jiang_ziya`|0|91|
|打神鞭|`task_wanxian_dashenbian`|0|76|
|榜上名|`task_wanxian_bangshangming`|0|87|
|玉虚天罚|`task_wanxian_yuxu_tianfa`|0|84|
|春日部巡逻队长|`task_kasukabe_patrol_captain`|0|94|
|玩具机关鳄鱼|`task_kasukabe_toy_crocodile`|0|92|
|迷路的防卫队员|`task_kasukabe_defense_member`|0|88|
|蜡笔恶作剧分身|`task_kasukabe_crayon_clone`|0|90|
|涂鸦大魔王|`task_kasukabe_doodle_boss`|0|88|
|蚩尤|`task_zhuolu_chiyou`|0|89|
|风伯|`task_zhuolu_fengbo`|0|88|
|雨师|`task_zhuolu_yushi`|0|82|
|食铁兽|`task_zhuolu_shitieshou`|0|87|
|黑风关守将|`guild_blackwind_gate_general`|0|95|
|单于|`task_barbarian_chanyu`|0|91|
|袁绍|`task_guandu_yuanshao`|0|89|
|吕布|`task_hulao_lvbu`|0|95|
|华雄|`task_sishui_huaxiong`|0|92|
|吕布|`task_baimenlou_lvbu`|0|93|
|高顺|`task_baimenlou_gaoshun`|0|92|
|张辽|`task_baimenlou_zhangliao`|0|87|
|陈宫|`task_baimenlou_chengong`|0|90|
|臧霸|`task_baimenlou_zangba`|0|89|
|黄裳|`task_huashan_huang_shang`|0|95|

## 备份与核对

- original_rosters.zip：本次修改前的11个YAML文件，包含此前已有的头像和属性修改。
- manifest.json：逐模板原文、新文、配置文件、修改后校验值以及本地数据库原简介。
- 已通过YAML解析、完整对象比较和数据库逐字段比较；只有目标flavor发生变化。
- 浏览器以本地admin实际检查关羽、朱棣、白起详情，新简介与数据库一致；手机页面文字正常显示，无页面脚本异常。
