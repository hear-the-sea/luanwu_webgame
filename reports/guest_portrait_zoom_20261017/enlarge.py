"""按人物轮廓等比放大头像，保留安全边距；生成并验证暂存结果后再应用。"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

# 数值库在首次导入时读取线程数，须先设置再导入。
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scipy.ndimage import binary_dilation  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REPORT = Path(__file__).resolve().parent
SOURCE = ROOT / "data/images/guests"
STAGE = Path("/tmp/webgame-guest-portrait-zoom-20261017")
SIZE = 448
MARGIN = 12

spec = importlib.util.spec_from_file_location(
    "portrait_normalizer", ROOT / "reports/guest_portrait_normalization_all_20261017/normalize.py"
)
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mask_for(image):
    pixels = np.asarray(image.convert("RGB")).astype(np.int16)
    low, high = pixels.min(axis=2), pixels.max(axis=2)
    return (low < 200) | ((high - low > 60) & (low < 240))


def subject_box(image):
    ys, xs = np.where(mask_for(image))
    if not len(xs):
        raise ValueError("未找到可识别的人物轮廓")
    return tuple(map(int, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)))


normalizer.subject_box = subject_box
normalizer.MAX_WIDTH = SIZE - 2 * MARGIN


def render(path):
    relative = path.relative_to(SOURCE)
    with Image.open(path) as raw:
        original_mode = raw.mode
        if original_mode == "RGBA":
            assert raw.getextrema()[3] == (255, 255), path
        original = raw.convert("RGB")
    before = subject_box(original)
    scale = min((SIZE - 2 * MARGIN) / (before[2] - before[0]), (SIZE - 2 * MARGIN) / (before[3] - before[1]))
    dimensions = (round(original.width * scale), round(original.height * scale))
    scaled_mask = Image.fromarray(np.uint8(mask_for(original)) * 255).resize(dimensions, Image.Resampling.NEAREST)
    left, top, right, bottom = scaled_mask.getbbox()
    offset = (round((SIZE - left - right) / 2), SIZE - MARGIN - bottom)
    scaled = original.resize(dimensions, Image.Resampling.LANCZOS)
    result = normalizer.background(original, *dimensions, *offset)
    yy, xx = np.mgrid[: dimensions[1], : dimensions[0]]
    distance = np.minimum.reduce([xx, yy, dimensions[0] - 1 - xx, dimensions[1] - 1 - yy]).astype(float)
    alpha = np.clip(distance / 12, 0, 1)
    preserve = binary_dilation(np.asarray(scaled_mask) > 0, iterations=2)
    alpha[preserve] = 1
    result.paste(scaled, offset, Image.fromarray(np.uint8(alpha * 255)))
    canvas_mask = Image.new("L", (SIZE, SIZE))
    canvas_mask.paste(scaled_mask, offset)
    assert np.count_nonzero(canvas_mask) == np.count_nonzero(scaled_mask), (path, "人物轮廓被截断")
    after = canvas_mask.getbbox()
    assert after and min(after[0], after[1], SIZE - after[2], SIZE - after[3]) >= MARGIN - 2, (
        path,
        "安全边距不足",
        after,
    )
    target = STAGE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if original_mode == "RGBA":
        result = result.convert("RGBA")
    if target.suffix.lower() in {".jpg", ".jpeg"}:
        result.save(target, quality=98, subsampling=0)
    elif target.suffix.lower() == ".webp":
        result.save(target, lossless=True)
    else:
        result.save(target, compress_level=6)
    with Image.open(target) as check:
        check.verify()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "before_size": list(original.size),
        "after_size": [SIZE, SIZE],
        "before_box": before,
        "after_box": after,
        "scale": scale,
        "offset": offset,
        "before_sha256": digest(path),
        "after_sha256": digest(target),
    }


def preview(records):
    selected = []
    for name in [
        "文037_男",
        "白起",
        "关羽",
        "朱棣",
        "杨逍",
        "韦一笑",
        "猛虎",
        "机关熊",
        "座山雕",
        "哪吒",
        "曹操",
        "女娲",
    ]:
        candidates = [r for r in records if Path(r["path"]).stem == name]
        if candidates:
            selected.append(candidates[0])
    for r in records[:: max(1, len(records) // 24)]:
        if r not in selected:
            selected.append(r)
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc", 12)
    sheet = Image.new("RGB", (6 * 264, ((len(selected) + 5) // 6) * 166), "#e5e7eb")
    draw = ImageDraw.Draw(sheet)
    for i, record in enumerate(selected):
        original = ROOT / record["path"]
        for column, file in enumerate([original, STAGE / original.relative_to(SOURCE)]):
            with Image.open(file) as image:
                image = image.convert("RGB")
                image.thumbnail((126, 126), Image.Resampling.LANCZOS)
            x, y = (i % 6) * 264 + column * 132, (i // 6) * 166
            sheet.paste(image, (x, y))
            draw.text((x + 3, y + 128), "调整前" if column == 0 else "调整后", font=font, fill="#222222")
        draw.text(((i % 6) * 264 + 3, (i // 6) * 166 + 146), original.stem, font=font, fill="#222222")
    sheet.save(REPORT / "comparison.jpg", quality=93)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    manifest_path = REPORT / "manifest.json"
    if not manifest_path.exists():
        files = sorted(p for p in SOURCE.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
        backup = REPORT / "originals.zip"
        if backup.exists():
            with zipfile.ZipFile(backup) as archive:
                assert len(archive.namelist()) == len(files)
                for path in files:
                    assert hashlib.sha256(archive.read(path.relative_to(ROOT).as_posix())).hexdigest() == digest(path)
        else:
            with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_STORED) as archive:
                for path in files:
                    archive.write(path, path.relative_to(ROOT))
        print(f"BACKUP_OK {len(files)}", flush=True)
        records = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for record in pool.map(render, files):
                records.append(record)
                if len(records) % 50 == 0:
                    print(f"STAGED {len(records)}/{len(files)}", flush=True)
        with zipfile.ZipFile(REPORT / "originals.zip") as archive:
            assert archive.testzip() is None
            for record in records:
                assert hashlib.sha256(archive.read(record["path"])).hexdigest() == record["before_sha256"]
        preview(records)
        manifest = {
            "count": len(records),
            "canvas": SIZE,
            "safe_margin": MARGIN,
            "status": "staged",
            "records": records,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    manifest = json.loads(manifest_path.read_text())
    if options.apply:
        assert manifest["status"] == "staged", "已应用过处理，禁止重复放大"
        assert (
            len(list(p for p in SOURCE.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}))
            == manifest["count"]
        )
        for record in manifest["records"]:
            assert digest(ROOT / record["path"]) == record["before_sha256"], record["path"]
        for record in manifest["records"]:
            target = ROOT / record["path"]
            temporary = target.with_name("." + target.name + ".zoom-tmp")
            assert not temporary.exists(), temporary
            shutil.copy2(STAGE / target.relative_to(SOURCE), temporary)
            os.replace(temporary, target)
        for record in manifest["records"]:
            assert digest(ROOT / record["path"]) == record["after_sha256"]
        manifest["status"] = "applied"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(f"APPLIED_AND_VERIFIED {manifest['count']}", flush=True)


if __name__ == "__main__":
    main()
