# 兑换碎片专属图标（2026-10-01）

使用内置 image_gen 工具分别生成 6 张独立图标，并复制原始 PNG 到项目资源目录。统一国风手绘道具风格、暖黄铜高光、左上方柔光、居中主体及很浅的浅黄色背景；不透明，无文字、边框或水印。

|物品|项目资源路径|
|---|---|
|诛仙剑意|data/images/items/zhuxian_sword_intent_v1.png|
|轩辕剑意|data/images/items/xuanyuan_sword_intent_v1.png|
|官渡帝王战章|data/images/items/guandu_emperor_fragment_v1.png|
|华山重剑心得|data/images/items/huashan_sword_fragment_v1.png|
|虎牢画戟战魂|data/images/items/hulao_halberd_fragment_v1.png|
|白门楼盘龙战魂|data/images/items/baimenlou_dragon_fragment_v1.png|

物品 YAML 与奖励重建脚本使用上述六个独立文件；兑换数量及奖励规则不变，发布状态以发布验收记录为准。资源导入继续使用项目已有的 200×200 WebP 压缩流程。

## 验证结果

- 6 张独立 PNG 均为 1254×1254、RGB、不透明，四角均为很浅的浅黄色，SHA-256 各不相同。
- 逐张调用项目现有压缩函数，均成功输出 200×200 WebP，大小约 4.5–7.6 KB。
- 物品导入与奖励兑换相关回归：20 passed，0 skipped，31.21 秒，日志 `/tmp/collection_fragment_icons_validation.log`。
- 重建脚本重复运行后物品配置完全一致，新图标引用不会被还原成旧占位图；Black、isort、flake8 和 diff 空白检查通过。

## 最终提示词

以下统一提示词与各项 Subject 拼接后分别提交给内置 image_gen 工具，transparent_background=false。

```text
Use case: stylized-concept. Asset type: single square inventory item icon for a Chinese wuxia strategy game, one of a coherent set of six collectible redemption fragments. Generate one standalone square image, NOT a sprite sheet or collage. Unified art direction: premium hand-painted 2D Chinese fantasy RPG item illustration, crisp dark-brown linework, warm antique-brass highlights, convincing painted material texture, elegant restrained detail, softly shaded three-quarter view, matching a hand-painted wood-and-brass wuxia treasure chest icon. Backdrop: uniform very pale light yellow #FFFBE8, opaque, very light cream-yellow, identical across the set, no scenery or texture, only a small soft contact shadow beneath the item. Composition: one centered collectible object occupying approximately 68% of the square, balanced generous clean margins, complete silhouette, no clipping, readable at 64px. Soft upper-left warm lighting, similar object scale across the set. No frame, no rarity border, no UI, no watermark, no labels, no letters or legible text, no people, no hands, no extra props. Subject:
```

### 诛仙剑意

```text
A single broken shard of an ancient dark steel sword blade, sharply recognizable blade silhouette, engraved flowing crimson celestial lines, delicate restrained red spiritual ribbon curling behind the shard; clearly a collectible shard rather than a complete sword.
```

### 轩辕剑意

```text
A single broken shard of a regal ancient golden-bronze sword blade, broad bevels and fine relief of sun and auspicious clouds, tiny restrained jade-green spiritual glow; clearly a collectible shard rather than a complete sword.
```

### 官渡帝王战章

```text
A single chipped imperial military medallion made of aged warm bronze with a sculpted ancient Chinese emperor helmet emblem in its center, jade-green enamel details and a short dark teal knot cord; asymmetric broken edge clearly makes it a collectible emblem fragment.
```

### 华山重剑心得

```text
A single torn piece of warm ivory parchment sword-training manuscript, lightly curled irregular torn edges, bound at one edge by a small dark teal ribbon, one bold black-ink silhouette diagram of a broad heavy sword and abstract brush marks with no legible writing, a restrained brass corner ornament; clearly a collectible manuscript fragment.
```

### 虎牢画戟战魂

```text
A single broken crescent-shaped side blade shard from an ancient Chinese halberd, dark steel center and engraved warm brass edge, one short vermilion tassel and a subtle crimson spiritual glow, strong crescent silhouette; clearly a collectible halberd fragment rather than a whole weapon.
```

### 白门楼盘龙战魂

```text
A single broken ornamental fragment from an ancient Chinese dragon-coiled war staff, warm bronze broken collar with a sculpted coiling dragon relief, short broken dark wooden core visible, jade-green inlay and a restrained jade-green spiritual glow; clearly a collectible staff fragment rather than an intact weapon.
```
