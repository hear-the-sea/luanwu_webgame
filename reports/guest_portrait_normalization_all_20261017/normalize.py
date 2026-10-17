"""统一其余门客图片；先备份和生成暂存结果，验证后再原位替换。"""

import hashlib
import json
import os
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from scipy.ndimage import binary_dilation  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/images/guests"
REPORT = Path(__file__).resolve().parent
STAGE = REPORT / "staged"
REFERENCE = SOURCE / "monkey/其他随机角色/文037_男.png"
PREVIOUS = ROOT / "reports/guest_portrait_normalization_20261017/manifest.json"
SIZE = 448
MAX_WIDTH = 392


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def subject_box(image):
    arr = np.asarray(image.convert("RGB"))
    ys, xs = np.where(arr.min(axis=2) < 130)
    if not len(xs):
        raise ValueError("未检测到人物深色轮廓")
    return tuple(map(int, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)))


def background(image, width, height, offset_x, offset_y):
    thumb = image.copy()
    thumb.thumbnail((192, 192), Image.Resampling.LANCZOS)
    arr = np.asarray(thumb, dtype=float)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    xn, yn = xx / max(w - 1, 1), yy / max(h - 1, 1)
    border = (xn < 0.12) | (xn > 0.88) | (yn < 0.10) | (yn > 0.94)
    # 从边缘最常见的浅色中识别背景，兼容米黄、浅粉、浅灰等原图底色。
    light = (arr.min(axis=2) > 150) & (arr.max(axis=2) - arr.min(axis=2) < 110)
    possible = arr[border & light]
    if len(possible) < 30:
        raise ValueError("原图背景样本不足")
    bins = np.floor(possible / 16).astype(int)
    code = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    dominant = np.bincount(code).argmax()
    center = np.median(possible[code == dominant], axis=0)
    chosen = border & light & (np.linalg.norm(arr - center, axis=2) < 36)
    x, y = xn[chosen][::3], yn[chosen][::3]
    values = arr[chosen][::3]
    design = np.column_stack([np.ones_like(x), x, y, x * x, x * y, y * y])
    keep = np.ones(len(values), dtype=bool)
    for _ in range(3):
        coeff = np.linalg.lstsq(design[keep], values[keep], rcond=None)[0]
        residual = np.linalg.norm(design @ coeff - values, axis=1)
        keep = residual < max(5, float(np.percentile(residual, 80)))
    oy, ox = np.mgrid[:SIZE, :SIZE]
    tx, ty = (ox - offset_x) / max(width - 1, 1), (oy - offset_y) / max(height - 1, 1)
    features = np.stack([np.ones_like(tx), tx, ty, tx * tx, tx * ty, ty * ty], axis=-1)
    fitted = features @ coeff
    lower, upper = np.percentile(values[keep], [2, 98], axis=0)
    return Image.fromarray(np.uint8(np.clip(np.clip(fitted, lower - 1, upper + 1), 0, 255)))


def normalize(image, box, ref):
    l, t, r, b = box
    scale = min((ref[3] - ref[1]) / (b - t), MAX_WIDTH / (r - l))
    w, h = image.size
    nw, nh = round(w * scale), round(h * scale)
    scaled = image.resize((nw, nh), Image.Resampling.LANCZOS)
    sl, st, sr, sb = subject_box(scaled)
    ox, oy = round((ref[0] + ref[2] - sl - sr) / 2), ref[3] - sb
    canvas = background(image, nw, nh, ox, oy)
    arr = np.asarray(scaled)
    yy, xx = np.mgrid[:nh, :nw]
    distance = np.minimum.reduce([xx, yy, nw - 1 - xx, nh - 1 - yy]).astype(float)
    alpha = np.clip(distance / 12, 0, 1)
    preserve = binary_dilation(arr.min(axis=2) < 205, iterations=2)
    alpha[preserve] = 1
    canvas.paste(scaled, (ox, oy), Image.fromarray(np.uint8(alpha * 255)))
    return canvas, scale, (ox, oy)


def render_one(task):
    index, path, ref = task
    old_hash = sha(path)
    with Image.open(path) as raw:
        original_mode = raw.mode
        original_size = raw.size
        # 当前输入的 RGBA 均为完全不透明，仍保持其文件模式。
        if raw.mode == "RGBA":
            assert raw.getextrema()[3] == (255, 255), (path, "发现真实透明图片，需单独处理")
        original = raw.convert("RGB")
    box = subject_box(original)
    result, scale, offset = normalize(original, box, ref)
    target = STAGE / path.relative_to(SOURCE)
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        result.save(target, quality=98, subsampling=0)
    else:
        if original_mode == "RGBA":
            result = result.convert("RGBA")
        result.save(target, compress_level=6)
    with Image.open(target) as check:
        check.verify()
    with Image.open(target) as check:
        after_box = subject_box(check)
        assert check.size == (SIZE, SIZE)
    l, t, r, b = after_box
    assert abs(b - ref[3]) <= 2, (path, "脚底", after_box)
    assert abs((l + r) - (ref[0] + ref[2])) <= 4, (path, "居中", after_box)
    assert min(l, t, SIZE - r, SIZE - b) >= 19, (path, "边距", after_box)
    assert r - l <= MAX_WIDTH + 4 and b - t <= ref[3] - ref[1] + 4, (path, "轮廓尺寸", after_box)
    return {
        "index": index,
        "path": path.relative_to(ROOT).as_posix(),
        "group": "root" if path.parent == SOURCE else "named",
        "before_size": original_size,
        "after_size": [SIZE, SIZE],
        "mode": original_mode,
        "before_box": box,
        "after_box": after_box,
        "scale": scale,
        "offset": offset,
        "before_sha256": old_hash,
        "after_sha256": sha(target),
        "source_touches_edge": min(box[0], box[1], original_size[0] - box[2], original_size[1] - box[3]) <= 2,
        "framed_card": original_size == (122, 122),
    }


def montage(records, archive, group, after):
    chosen = [r for r in records if r["group"] == group]
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc", 12)
    for start in range(0, len(chosen), 30):
        page = chosen[start : start + 30]
        sheet = Image.new("RGB", (6 * 164, ((len(page) + 5) // 6) * 188), "#e5e7eb")
        draw = ImageDraw.Draw(sheet)
        for i, r in enumerate(page):
            stream = ROOT / r["path"] if after else archive.open(r["path"])
            with Image.open(stream) as img:
                img = img.convert("RGB")
                img.thumbnail((156, 156), Image.Resampling.LANCZOS)
            x, y = (i % 6) * 164 + 4, (i // 6) * 188 + 4
            sheet.paste(img, (x + (156 - img.width) // 2, y + (156 - img.height) // 2))
            name = Path(r["path"]).stem
            draw.text((x, y + 158), f"{r['index']:03d} {name[:15]}", font=font, fill="#111827")
            draw.text(
                (x, y + 172),
                f"h {r['before_box'][3]-r['before_box'][1]} → {r['after_box'][3]-r['after_box'][1]}",
                font=font,
                fill="#111827",
            )
        label = "after" if after else "before"
        sheet.save(REPORT / f"{group}_{label}_{start//30+1:02d}.jpg", quality=92)


def main():
    old = json.loads(PREVIOUS.read_text())
    unchanged = {r["path"]: r["after_sha256"] for r in old["records"]}
    for path, digest in unchanged.items():
        assert sha(ROOT / path) == digest, (path, "上次处理后文件发生变化")
    files = [
        p
        for p in sorted(SOURCE.rglob("*"))
        if p.is_file()
        and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        and p.relative_to(ROOT).as_posix() not in unchanged
    ]
    assert len(files) == 575, len(files)
    with Image.open(REFERENCE) as image:
        ref = subject_box(image)
    backup = REPORT / "originals.zip"
    if (REPORT / "manifest.json").exists():
        raise RuntimeError("已完成过处理，禁止重复缩放")
    if backup.exists():
        with zipfile.ZipFile(backup) as archive:
            for path in files:
                assert hashlib.sha256(archive.read(path.relative_to(ROOT).as_posix())).hexdigest() == sha(path)
    else:
        with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in files:
                archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"BACKUP_OK count={len(files)} bytes={backup.stat().st_size}", flush=True)
    tasks = [(i, p, ref) for i, p in enumerate(files, 1)]
    records = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for record in pool.map(render_one, tasks):
            records.append(record)
            if len(records) % 50 == 0:
                print(f"STAGED {len(records)}/{len(files)}", flush=True)
    manifest = {
        "reference": REFERENCE.relative_to(ROOT).as_posix(),
        "reference_box": ref,
        "count": len(records),
        "previous_count": len(unchanged),
        "total": len(records) + len(unchanged),
        "groups": dict(Counter(r["group"] for r in records)),
        "records": records,
    }
    # 暂存全部生成成功且验证通过后才替换源图，替换前再次检查用户没有并发修改。
    for r in records:
        assert sha(ROOT / r["path"]) == r["before_sha256"]
    (REPORT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for r in records:
        path = ROOT / r["path"]
        os.replace(STAGE / path.relative_to(SOURCE), path)
    for path, digest in unchanged.items():
        assert sha(ROOT / path) == digest
    print("APPLIED_OK", len(records), flush=True)
    with zipfile.ZipFile(backup) as archive:
        assert archive.testzip() is None
        for r in records:
            assert hashlib.sha256(archive.read(r["path"])).hexdigest() == r["before_sha256"]
            assert sha(ROOT / r["path"]) == r["after_sha256"]
        for group in ("root", "named"):
            for after in (False, True):
                montage(records, archive, group, after)
    print("VERIFIED_OK", manifest["total"], flush=True)


if __name__ == "__main__":
    main()
