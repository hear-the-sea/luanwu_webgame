"""为赛季静态图片补齐 Caddy WebP 副本，保留原图及 URL。"""

import hashlib
import json
import shutil
import time
from pathlib import Path

from PIL import Image

from scripts.prepare_webp_images import prepare_image

root = Path("/app/staticfiles")
manifest = json.loads((root / "staticfiles.json").read_text())["paths"]
rows = []
for name, hashed in manifest.items():
    if not name.startswith(("season-assets/", "season-troops/")) or not name.endswith(".png"):
        continue
    source = root / name
    target = root / hashed
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest(), name
    assert shutil.disk_usage(root).free > 128 * 1024 * 1024
    result = prepare_image(source, apply=True, quality=85)
    companion = source.with_name(source.name + ".webp")
    with Image.open(source) as original, Image.open(companion) as optimized:
        assert original.size == optimized.size
        assert optimized.format == "WEBP"
        if "A" in original.getbands() and original.getchannel("A").getextrema()[0] < 255:
            assert "A" in optimized.getbands()
            assert original.getchannel("A").tobytes() == optimized.getchannel("A").tobytes(), "Alpha mismatch: " + name
    hashed_companion = target.with_name(target.name + ".webp")
    existed = hashed_companion.exists()
    if companion != hashed_companion:
        temporary = hashed_companion.with_name(hashed_companion.name + ".tmp")
        shutil.copyfile(companion, temporary)
        temporary.replace(hashed_companion)
    row = {
        "name": name,
        "hashed": hashed,
        "source_bytes": source.stat().st_size,
        "webp_bytes": companion.stat().st_size,
        "previous_hashed_companion": existed,
    }
    rows.append(row)
    print(json.dumps({"completed": len(rows), "name": name, "webp_bytes": row["webp_bytes"]}), flush=True)
print(
    json.dumps(
        {
            "status": "complete",
            "images": rows,
            "source_bytes": sum(r["source_bytes"] for r in rows),
            "webp_bytes": sum(r["webp_bytes"] for r in rows),
        }
    ),
    flush=True,
)
