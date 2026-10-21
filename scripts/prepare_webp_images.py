"""生成供 Caddy 优先读取的 WebP 副本，保留原文件及所有已有 URL。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from PIL import Image, ImageSequence

SOURCE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})


def prepare_image(source: Path, *, apply: bool = False, quality: int = 85) -> dict:
    destination = source.with_name(source.name + ".webp")
    result = {"path": str(source), "source_bytes": source.stat().st_size}
    fingerprint = f"webgame-webp-q{quality}:" + hashlib.sha256(source.read_bytes()).hexdigest()
    if destination.exists():
        with Image.open(destination) as previous:
            if previous.format == "WEBP" and previous.getexif().get(270) == fingerprint:
                result.update(status="current", webp_bytes=destination.stat().st_size)
                return result
    if not apply:
        return {**result, "status": "planned"}

    temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
    try:
        with Image.open(source) as image:
            frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(image)]
            metadata = Image.Exif()
            metadata[270] = fingerprint
            options = {"format": "WEBP", "quality": quality, "method": 6, "exif": metadata}
            if len(frames) > 1:
                options.update(
                    save_all=True,
                    append_images=frames[1:],
                    duration=[frame.info.get("duration", 100) for frame in ImageSequence.Iterator(image)],
                    loop=image.info.get("loop", 0),
                )
            frames[0].save(temporary, **options)
            with Image.open(temporary) as check:
                assert check.format == "WEBP" and check.size == image.size
                assert getattr(check, "n_frames", 1) == len(frames)
                check.load()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {**result, "status": "converted", "webp_bytes": destination.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write WebP files; otherwise only report the plan.")
    args = parser.parse_args()
    os.nice(10)
    rows = []
    for root in args.root:
        if not root.is_dir():
            parser.error(f"Image root does not exist: {root}")
        for source in sorted(root.rglob("*")):
            if source.is_file() and not source.is_symlink() and source.suffix.lower() in SOURCE_EXTENSIONS:
                rows.append(prepare_image(source, apply=args.apply))
    print(json.dumps({"images": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
