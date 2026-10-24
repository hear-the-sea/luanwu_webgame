# 小昭招募路线

## 玩家规则

- 围攻光明顶通关后，按每位玩家报名时的三名门客独立结算。三名均为蓝色或绿色（可混搭）：固定火种50个，50%概率获得明教圣火令1个；其他阵容：固定火种10个，10%概率获得明教圣火令1个。
- 失败不发上述材料；通关材料不另设伤害贡献门槛。原有角斗币及稀有图纸奖励不变。
- 火种、明教圣火令均不可交易，默认绑定。
- 竞技场兑换：50角斗币兑换1个火种，可批量兑换，按数量扣费。
- 商铺无限出售小昭招募卷轴，沿用祝英台紫色卷轴定价（模板回收价600万，购买按现有商铺倍率计算）。
- 在仓库使用卷轴，消耗卷轴1个、圣火令12个和火种1000个，获得紫色文门客小昭。成长6～12，倾向智力和敏捷。庄园已有小昭（含升阶后的小昭）不能重复召唤。
- 材料不足、已拥有、满员或召唤异常时，不扣卷轴和材料；沿用现有庄园锁和事务。
- 已创建活动继续使用原奖励快照，新建活动启用新奖励。

## 定向数据同步

执行 `python manage.py sync_mingjiao_recruitment`，仅同步小昭和本次三个物品模板及图片，不执行全库门客清理，不修改玩家库存。

命令已经在本地执行成功。部署时将代码、配置和对应图片一并更新后执行该命令；运行进程需要加载新代码和配置。

## 图片

使用内置 image_gen 工具生成，未使用 CLI。原始 PNG 保存在：

- `data/images/items/mingjiao_holy_fire_token.png`
- `data/images/items/mingjiao_fire_seed.png`
- `data/images/items/xiaozhao_recruit_scroll.png`

入库使用项目现有图片压缩流程，得到200×200 WebP。三张均已人工查看。

实际生成提示词：

### 明教圣火令

Use case: stylized-concept. Asset type: square inventory item illustration for a Chinese wuxia game. Primary request: 明教圣火令, one ancient ivory-white and dark metal elongated sacred command tablet with a flame-shaped crest and subtle Persian ornamental engravings, small red tassel. Style: hand-painted Chinese traditional 2D game item icon, delicate warm brown ink outlines and watercolor shading on a very pale light-yellow background (#fff9df). Centered single object fully visible, large readable silhouette and generous margin. No lettering, no numbers, no frame, no UI, no watermark. It must look like a tangible martial-arts token, not a weapon or playing card.

### 火种

Use case: stylized-concept. Asset type: square inventory item icon in Chinese wuxia 2D game. Subject: 火种, one small precious vivid orange-red sacred flame glowing from a simple little bronze oil lamp cup, tiny warm sparks, clear flame silhouette, no larger scene. Hand-painted Chinese style, warm brown ink outlines, gentle watercolor shading, antique bronze with understated traditional ornament. Very pale light yellow background #fff9df, centered object with generous margin, flat 2D illustration. No characters, no letters, no labels, no frame, no watermark. Match a traditional hand-painted Chinese RPG inventory illustration.

### 小昭招募卷轴

Use case: stylized-concept. Asset type: square inventory item illustration for a Chinese wuxia 2D game. Subject: 小昭招募卷轴, a single elegant parchment invitation scroll, half unrolled with a purple silk tie and tassels, carved wooden finials, a small red wax seal bearing a simple sacred flame symbol; subtle pale-gold Persian-inspired ornamental borders evoke Xiaozhao and Ming Cult. No person portrait. Hand-painted traditional Chinese 2D style, warm brown outlines and gentle watercolor shading, clean very pale light yellow background #fff9df. Centered object fully visible with ample margins, readable at small inventory size. No writing, no lettering, no text, no frame, no UI, no watermark.

## 验证

共斗新增奖励、概率边界、报名快照、零贡献通关、消息及重复结算测试和既有共斗报名结算回归37项通过；召唤配方及已有门客道具回归58项通过；小昭定向同步3项通过；兑换、不可交易、商铺购买及完整同步5项通过；YAML配置12项通过。Django系统检查无问题，相关Python文件格式和静态检查通过。
