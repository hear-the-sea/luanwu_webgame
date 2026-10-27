"""图片处理工具"""

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Protocol, Tuple, cast

from django.core.files.base import ContentFile
from PIL import Image, ImageSequence

DEFAULT_TRANSPARENT_BACKGROUND_COLOR = (248, 248, 232)


class SeekableBinaryFile(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def seek(self, offset: int, whence: int = 0) -> int: ...

    def tell(self) -> int: ...


def flatten_transparent_image(image: Image.Image) -> Image.Image:
    """将透明区域合成到与现有门客素材接近的浅暖黄色底色。"""
    rgba_image = image.convert("RGBA")
    background = Image.new("RGBA", rgba_image.size, (*DEFAULT_TRANSPARENT_BACKGROUND_COLOR, 255))
    return Image.alpha_composite(background, rgba_image).convert("RGB")


def prepare_webp_frame(image: Image.Image) -> Image.Image:
    """转换为 WebP 支持的 RGB/RGBA 模式，并保留源图透明通道。"""
    has_transparency = "A" in image.getbands() or "transparency" in image.info
    return image.convert("RGBA" if has_transparency else "RGB")


def convert_image_content_to_webp(
    image_file: SeekableBinaryFile,
    original_name: str,
    max_size: Tuple[int, int] | None = None,
    quality: int = 85,
) -> Tuple[ContentFile, str]:
    """把上传图片统一转为 WebP，并保留透明通道及动画帧。"""
    try:
        image_file.seek(0)
    except (AttributeError, OSError):
        pass
    output_name = f"{Path(original_name).stem or 'image'}.webp"
    with Image.open(cast(BinaryIO, image_file)) as image:
        frame_count = getattr(image, "n_frames", 1)
        has_transparency = "A" in image.getbands() or "transparency" in image.info
        exceeds_max_size = max_size is not None and (image.width > max_size[0] or image.height > max_size[1])

        # 已经是符合限制的不透明 WebP 时直接复用编码数据，避免无谓的二次有损压缩。
        if image.format == "WEBP" and frame_count == 1 and not has_transparency and not exceeds_max_size:
            image_file.seek(0)
            source_bytes = image_file.read()
            return ContentFile(source_bytes), output_name

        loop = image.info.get("loop", 0)
        frames = []
        durations = []
        for frame in ImageSequence.Iterator(image):
            prepared = prepare_webp_frame(frame)
            if max_size is not None:
                if prepared.mode == "RGBA":
                    prepared = prepared.convert("RGBa")
                prepared.thumbnail(max_size, Image.Resampling.LANCZOS)
                if prepared.mode == "RGBa":
                    prepared = prepared.convert("RGBA")
            frames.append(prepared)
            durations.append(frame.info.get("duration", image.info.get("duration", 100)))

        buffer = BytesIO()
        options: dict[str, Any] = {"format": "WEBP", "quality": quality, "method": 6}
        if len(frames) > 1:
            options.update(save_all=True, append_images=frames[1:], duration=durations, loop=loop)
        frames[0].save(buffer, **options)
    return ContentFile(buffer.getvalue()), output_name


def get_file_hash(file_path: Path) -> str:
    """
    计算文件的 MD5 哈希值

    Args:
        file_path: 文件路径

    Returns:
        MD5 哈希值（16进制字符串）
    """
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def compress_and_resize_image(
    image_path: Path,
    max_size: Tuple[int, int] = (400, 400),
    quality: int = 85,
    convert_to_webp: bool = True,
) -> Tuple[ContentFile, str]:
    """
    压缩并调整图片大小

    Args:
        image_path: 原始图片路径
        max_size: 最大尺寸 (width, height)
        quality: 压缩质量 (1-100)
        convert_to_webp: 是否转换为 WebP 格式

    Returns:
        (压缩后的文件对象, 文件名)
    """
    if convert_to_webp:
        with image_path.open("rb") as image_file:
            return convert_image_content_to_webp(
                image_file,
                image_path.name,
                max_size=max_size,
                quality=quality,
            )

    with Image.open(image_path) as image:
        image_format = image.format or "JPEG"
        prepared = flatten_transparent_image(image)
        prepared.thumbnail(max_size, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        prepared.save(buffer, format=image_format, quality=quality, optimize=True)
    return ContentFile(buffer.getvalue()), image_path.name


def get_image_info(image_path: Path) -> dict:
    """
    获取图片信息

    Args:
        image_path: 图片路径

    Returns:
        包含图片信息的字典
    """
    with Image.open(image_path) as img:
        return {
            "width": img.width,
            "height": img.height,
            "format": img.format,
            "mode": img.mode,
            "size_bytes": image_path.stat().st_size,
        }
