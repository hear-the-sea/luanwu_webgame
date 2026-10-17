"""按参考人物边界等比缩放随机头像，保留原图备份与逐图审计。"""

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_dilation

ROOT = Path("/home/daniel/code/web_game_v5")
SOURCE = ROOT / "data/images/guests"
REPORT = ROOT / "reports/guest_portrait_normalization_20261017"
REFERENCE = SOURCE / "monkey/其他随机角色/文037_男.png"
GROUPS = [SOURCE / "monkey/其他随机角色", SOURCE / "pig"]
SIZE = 448


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def subject_box(image):
    # 深色轮廓用于测量，排除浅色背景和脚下阴影；保留全部道具与尾巴。
    rgb = np.asarray(image.convert("RGB"))
    ys, xs = np.where(rgb.min(axis=2) < 130)
    if not len(xs):
        raise ValueError("未检测到人物轮廓")
    return tuple(map(int, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)))


def background(image, width, height, offset_x, offset_y):
    # 使用背景亮色拟合平滑曲面，补齐移动后露出的画布，避免拉长边缘人物。
    arr = np.asarray(image.convert("RGB"), dtype=np.float64)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    xn, yn = xx / max(w - 1, 1), yy / max(h - 1, 1)
    pale = (arr[:, :, 0] > 230) & (arr[:, :, 1] > 225) & (arr[:, :, 2] > 195)
    border = (xn < 0.12) | (xn > 0.88) | (yn < 0.10) | (yn > 0.94)
    sample = pale & border
    y, x = yn[sample][::5], xn[sample][::5]
    values = arr[sample][::5]
    if len(x) < 30:
        raise ValueError("背景采样不足")
    design = np.column_stack([np.ones_like(x), x, y, x * x, x * y, y * y])
    coeff = np.linalg.lstsq(design, values, rcond=None)[0]
    oy, ox = np.mgrid[:SIZE, :SIZE]
    tx = (ox - offset_x) / max(width - 1, 1)
    ty = (oy - offset_y) / max(height - 1, 1)
    features = np.stack([np.ones_like(tx), tx, ty, tx * tx, tx * ty, ty * ty], axis=-1)
    predicted = features @ coeff
    lower, upper = np.percentile(values, [2, 98], axis=0)
    predicted = np.clip(predicted, lower - 1, upper + 1)
    return Image.fromarray(np.uint8(np.clip(predicted, 0, 255)))


def normalize(image, box, ref_box):
    left, top, right, bottom = box
    ref_left, ref_top, ref_right, ref_bottom = ref_box
    target_height = ref_bottom - ref_top
    # 横向舒展的角色保留至少约 6% 的两侧余量，不横向压缩人物。
    max_width = 392
    scale = min(target_height / (bottom - top), max_width / (right - left))
    w, h = image.size
    new_w, new_h = round(w * scale), round(h * scale)
    scaled = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    scaled_box = subject_box(scaled)
    offset_x = round((ref_left + ref_right) / 2 - (scaled_box[0] + scaled_box[2]) / 2)
    offset_y = ref_bottom - scaled_box[3]
    canvas = background(image, new_w, new_h, offset_x, offset_y)
    # 仅在源图外边缘淡化亮色背景，保留人物像素，缓解背景拼接色差。
    pixels = np.asarray(scaled)
    y, x = np.mgrid[:new_h, :new_w]
    distance = np.minimum.reduce([x, y, new_w - 1 - x, new_h - 1 - y]).astype(float)
    alpha = np.clip(distance / 12, 0, 1)
    preserve = binary_dilation(pixels.min(axis=2) < 205, iterations=2)
    alpha[preserve] = 1
    mask = Image.fromarray(np.uint8(alpha * 255))
    canvas.paste(scaled, (offset_x, offset_y), mask)
    return canvas, scale, (offset_x, offset_y)


def montage(records, originals, group, after):
    chosen = [r for r in records if r["group"] == group]
    font_paths = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font = ImageFont.truetype(str(font_paths[0]), 13) if font_paths[0].exists() else ImageFont.load_default()
    for page_start in range(0, len(chosen), 24):
        page = chosen[page_start : page_start + 24]
        columns = 6
        rows = (len(page) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * 176, rows * 199), "#e5e7eb")
        draw = ImageDraw.Draw(sheet)
        for i, record in enumerate(page):
            path = ROOT / record["path"]
            image = Image.open(path).convert("RGB") if after else originals[path]
            image = image.resize((168, 168), Image.Resampling.LANCZOS)
            x, y = (i % columns) * 176 + 4, (i // columns) * 199 + 4
            sheet.paste(image, (x, y))
            draw.text(
                (x, y + 171),
                f"{record['index']:03d} {record['before_box'][3]-record['before_box'][1]}->{record['after_box'][3]-record['after_box'][1]}",
                fill="#111827",
                font=font,
            )
        label = "after" if after else "before"
        sheet.save(REPORT / f"{group}_{label}_{page_start//24+1}.jpg", quality=92)


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    backup = REPORT / "originals.zip"
    if backup.exists():
        raise RuntimeError("备份已存在，禁止覆盖原始备份或重复处理")
    files = [p for group in GROUPS for p in sorted(group.glob("*.png"))]
    assert len(files) == 117, len(files)
    originals = {path: Image.open(path).convert("RGB") for path in files}
    reference_hash = sha(REFERENCE)
    ref_box = subject_box(originals[REFERENCE])
    with zipfile.ZipFile(backup, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    records = []
    for index, path in enumerate(files, 1):
        original = originals[path]
        box = subject_box(original)
        before_hash = sha(path)
        if path == REFERENCE:
            result = original
            scale, offset = 1, (0, 0)
        else:
            result, scale, offset = normalize(original, box, ref_box)
            result.save(path, optimize=True)
        new_box = subject_box(result)
        assert result.size == (SIZE, SIZE)
        assert abs(new_box[3] - ref_box[3]) <= 1, (path, new_box)
        assert abs((new_box[0] + new_box[2]) - (ref_box[0] + ref_box[2])) <= 2, (path, new_box)
        assert new_box[0] >= 20 and new_box[2] <= SIZE - 20, (path, new_box)
        assert new_box[1] >= 20 and new_box[3] <= SIZE - 20, (path, new_box)
        assert max(new_box[2] - new_box[0], new_box[3] - new_box[1]) <= 394
        records.append(
            {
                "index": index,
                "path": path.relative_to(ROOT).as_posix(),
                "group": "monkey" if path.parent == GROUPS[0] else "pig",
                "before_size": original.size,
                "after_size": result.size,
                "before_box": box,
                "after_box": new_box,
                "scale": scale,
                "offset": offset,
                "before_sha256": before_hash,
                "after_sha256": sha(path),
            }
        )
    assert sha(REFERENCE) == reference_hash
    manifest = {
        "reference": REFERENCE.relative_to(ROOT).as_posix(),
        "reference_box": ref_box,
        "target_height": ref_box[3] - ref_box[1],
        "max_width": 392,
        "baseline": ref_box[3],
        "count": len(records),
        "backup": backup.relative_to(ROOT).as_posix(),
        "records": records,
    }
    (REPORT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for group in ("monkey", "pig"):
        for after in (False, True):
            montage(records, originals, group, after)
    print(json.dumps({k: v for k, v in manifest.items() if k != "records"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
