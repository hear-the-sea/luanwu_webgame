# 专属门客道具生图记录

工具：内置 `image_gen.imagegen`，三次独立生成，非透明背景。参考图片仅用于风格：`data/images/items/guest_rebirth_card.png`、`skill3.png`、`experience_fruit.png`。

三次请求共用以下提示词前缀：

```text
Use case: stylized-concept. Asset type: square inventory item icon for a Chinese wuxia browser game. Match the provided reference icons: warm pale ivory parchment background, delicate dark-brown hand-drawn outlines, softly painted muted watercolor shading, subtle paper grain, one large centered object, generous clear margin. No UI frame, no rarity colored border, no labels, no English text, no watermark, no photorealism, no neon, no modern devices. Output one square icon. References are style only, create a new object.
```

`custom_guest_card.png` 附加提示词：

```text
Subject: a gently tilted old parchment summoning card with ornate olive-green and bronze corners, a small seated human silhouette formed from swirling jade-green spiritual ink in its center, a faint circular soul seal behind the figure. Evokes a transmigrated soul receiving a personal body; keep it an elegant physical card, consistent with the plant card reference.
```

`yijinjing.png` 附加提示词：

```text
Subject: a single ancient Chinese stitched martial-arts manual, at a three-quarter angle, muted indigo cloth cover, ivory pages, antique brass-gold corner ornament and a narrow jade bookmark. Its small cream title slip bears only the three clearly legible Chinese characters 易筋经 vertically. Rich but restrained hand-painted detail, matching the old skill-book reference.
```

`millennium_zhuguo.png` 附加提示词：

```text
Subject: a single ripe vermilion-red mystical fruit on a short woody stem with two jade-green leaves, a tiny golden fleck and restrained warm aura suggesting a thousand years of cultivation. Clearly a precious botanical fruit, large round smooth red skin, no face. Match the berry reference painting and parchment background.
```

最终保存位置为仓库的 `data/images/items/`，原始生成结果保留在Codex生成图片目录。已目视核对三张图的物品主体、纸底、描边与现有游戏道具风格相符。
