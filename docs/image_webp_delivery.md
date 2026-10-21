# 图片 WebP 交付

静态目录和媒体目录中的 PNG、JPEG 保留原文件，并生成 `<原文件名>.webp` 副本。
例如 `3v3车轮战.png.webp` 对应 `3v3车轮战.png`，不会与同名 JPEG 或已有 WebP 冲突。
Caddy 优先读取副本并返回 `Content-Type: image/webp`；不存在副本时仍读取原文件。
旧图片 URL、数据库路径、静态 manifest 及缓存规则保持兼容，SVG 等非位图资源继续使用原格式。
现有浏览器缓存不会被主动清除。

在收集静态文件、导入旧格式媒体或更新原图后运行：

```bash
python scripts/prepare_webp_images.py --root staticfiles --root media
python scripts/prepare_webp_images.py --root staticfiles --root media --apply
```

第一条只查看计划，第二条生成副本。转换保持像素尺寸、透明通道及动画帧；默认质量 85。
副本嵌入源文件 SHA-256 和质量标识，重复执行跳过未变化的原图。
写入使用临时文件及原子替换，避免浏览器读到不完整内容。
Docker 入口在 `DJANGO_COLLECTSTATIC=1` 时自动执行上述转换；worker 不重复转换。

线上文件位于 `/root/code/runtime/staticfiles` 和 `/root/code/runtime/media`，
对应 Web 容器的 `/app/staticfiles` 和 `/app/media`。
旧镜像可通过 `docker exec -i code-web-1 python - --root /app/staticfiles --root /app/media --apply`
从标准输入运行新版脚本，不必重启应用。
先完成转换，验证 Caddy 配置，再平滑 reload；回退时恢复先前 Caddyfile 并 reload 即可。

2026-10-21 的 MySQL 扩容使用服务器 `.env.docker` 中的
`MYSQL_MEMORY_LIMIT=3072m`，并通过 `docker update --memory 3g code-db-1` 在线生效。
数据库容器原来的内存加 swap 合计限额 4 GiB 保持不变；没有调整缓冲池或重启数据库。
