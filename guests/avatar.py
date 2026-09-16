"""门客头像解析及按文武/性别选择头像资源。"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage

from core.utils.image_utils import compress_and_resize_image

from .constants import GUEST_DISGUISE_AVATAR_PREFIXES, SUPER_GUEST_DISGUISE_AVATAR_KEY, SUPER_GUEST_DISGUISE_AVATAR_PATH

logger = logging.getLogger(__name__)

GUEST_AVATAR_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
GUEST_AVATAR_STORAGE_PREFIX = "guests/"
INSTANCE_AVATAR_TEMPLATE_KEYS = frozenset({"base_gray_civil", "base_gray_military"})
GUEST_DISGUISE_AVATAR_LABELS = {
    "monkey": "猴",
    "pig": "猪",
    "duck": "鸭",
}
GUEST_DISGUISE_AVATAR_PART_LABELS = {
    "wen": "文",
    "wu": "武",
    "male": "男",
    "female": "女",
    "special": "特别",
    "mission": "任务",
    "mistry": "神秘",
}


def uses_instance_avatar_generation(template_key: str | None) -> bool:
    """判断门客模板是否应在生成门客实例时按性别选择头像。"""
    return template_key in INSTANCE_AVATAR_TEMPLATE_KEYS


def extract_guest_avatar_tags(filename: str) -> tuple[str | None, str | None]:
    """从头像文件名提取文/武和男/女标签。"""
    normalized = filename.lower().replace("-", "_")
    archetype = None
    if "_wen_" in normalized or "_wen." in normalized:
        archetype = "civil"
    elif "_wu_" in normalized or "_wu." in normalized:
        archetype = "military"

    gender = None
    if "_female" in normalized:
        gender = "female"
    elif "_male" in normalized:
        gender = "male"
    return archetype, gender


def build_guest_avatar_catalog(image_source_dir: Path) -> dict[tuple[str, str], list[str]]:
    """构建按文武和性别分组的头像源文件目录。"""
    catalog: dict[tuple[str, str], list[str]] = {
        ("civil", "male"): [],
        ("civil", "female"): [],
        ("military", "male"): [],
        ("military", "female"): [],
    }
    if not image_source_dir.exists():
        return catalog

    for path in image_source_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in GUEST_AVATAR_EXTENSIONS:
            continue
        archetype, gender = extract_guest_avatar_tags(path.name)
        if archetype and gender:
            catalog[(archetype, gender)].append(path.name)

    for filenames in catalog.values():
        filenames.sort()
    return catalog


@lru_cache(maxsize=1)
def get_guest_avatar_catalog() -> dict[tuple[str, str], list[str]]:
    """读取默认头像目录；进程内缓存，避免每次召唤扫描文件系统。"""
    source_dir = Path(settings.BASE_DIR) / "data" / "images" / "guests"
    return build_guest_avatar_catalog(source_dir)


def pick_guest_avatar_filename(
    avatar_catalog: dict[tuple[str, str], list[str]],
    archetype: str | None,
    gender: str | None,
    *,
    seed_key: str | None = None,
    rng: random.Random | None = None,
    fallback_to_any: bool = True,
) -> str | None:
    """从头像目录选择头像；召唤流程可关闭跨类别兜底以保证标签匹配。"""
    candidates: Sequence[str] = ()
    if archetype in {"civil", "military"} and gender in {"male", "female"}:
        candidates = avatar_catalog.get((archetype, gender), [])

    if not candidates and fallback_to_any:
        any_candidates: list[str] = []
        if archetype in {"civil", "military"}:
            any_candidates.extend(avatar_catalog.get((archetype, "male"), []))
            any_candidates.extend(avatar_catalog.get((archetype, "female"), []))
        if not any_candidates:
            for filenames in avatar_catalog.values():
                any_candidates.extend(filenames)
        candidates = any_candidates

    if not candidates:
        return None
    chooser = rng or random.Random(seed_key or "")
    return chooser.choice(list(candidates))


def ensure_guest_avatar_asset(source_filename: str) -> str:
    """将头像源图压缩到媒体目录并返回受控的存储路径。"""
    source_dir = Path(settings.BASE_DIR) / "data" / "images" / "guests"
    source_path = source_dir / source_filename
    if source_path.name != source_filename or not source_path.is_file():
        return ""

    target_name = f"{GUEST_AVATAR_STORAGE_PREFIX}{source_path.stem}.webp"
    try:
        if default_storage.exists(target_name):
            return target_name
        compressed_file, _new_filename = compress_and_resize_image(
            source_path,
            max_size=(400, 400),
            quality=85,
            convert_to_webp=True,
        )
        return default_storage.save(target_name, compressed_file)
    except OSError as exc:
        logger.warning("Failed to prepare guest avatar %s: %s", source_filename, exc)
        return ""


def get_guest_disguise_avatar_filenames() -> tuple[str, ...]:
    """返回门客易容卡可使用的受控头像文件。"""
    source_dir = Path(settings.BASE_DIR) / "data" / "images" / "guests"
    if not source_dir.exists():
        return ()

    filenames = []
    for path in source_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in GUEST_AVATAR_EXTENSIONS:
            continue
        normalized_name = path.name.lower()
        if any(normalized_name.startswith(prefix) for prefix in GUEST_DISGUISE_AVATAR_PREFIXES):
            filenames.append(path.name)
    prefix_rank = {prefix: index for index, prefix in enumerate(GUEST_DISGUISE_AVATAR_PREFIXES)}

    def sort_key(filename: str) -> tuple[int, str]:
        normalized_name = filename.lower()
        rank = next(
            (prefix_rank[prefix] for prefix in GUEST_DISGUISE_AVATAR_PREFIXES if normalized_name.startswith(prefix)),
            len(prefix_rank),
        )
        return rank, normalized_name

    return tuple(sorted(filenames, key=sort_key))


def _guest_disguise_avatar_label(filename: str) -> str:
    stem = Path(filename).stem
    normalized_stem = stem.lower()
    prefix = next(
        (candidate for candidate in GUEST_DISGUISE_AVATAR_PREFIXES if normalized_stem.startswith(candidate)),
        "",
    )
    prefix_label = GUEST_DISGUISE_AVATAR_LABELS.get(prefix, "门客")
    suffix = stem[len(prefix) :].lstrip("_-") if prefix else stem
    label_parts = []
    for part in suffix.replace("-", "_").split("_"):
        normalized_part = part.lower()
        # 文件名中的文/武仅用于区分资源，玩家选择形象时不需要看到这层技术标记。
        if normalized_part in {"wen", "wu"}:
            continue
        label = next(
            (
                f"{translated}{normalized_part[len(english):]}"
                for english, translated in GUEST_DISGUISE_AVATAR_PART_LABELS.items()
                if normalized_part.startswith(english)
            ),
            part,
        )
        label_parts.append(label or part)
    detail = "".join(label_parts)
    return f"{prefix_label}·{detail}" if detail else f"{prefix_label}形象"


def get_guest_disguise_avatar_options() -> list[dict[str, str]]:
    """准备门客易容卡弹窗展示的头像选项及名称。"""
    options: list[dict[str, str]] = []
    for filename in get_guest_disguise_avatar_filenames():
        asset_path = ensure_guest_avatar_asset(filename)
        if not asset_path:
            continue
        try:
            asset_url = str(default_storage.url(asset_path) or "")
        except (AttributeError, ValueError):
            asset_url = ""
        if not asset_url:
            continue
        options.append(
            {
                "key": filename,
                "label": _guest_disguise_avatar_label(filename),
                "url": asset_url,
            }
        )
    return options


def resolve_guest_disguise_avatar_asset(source_filename: object) -> str:
    """通过受控素材目录解析客户端选择的头像。"""
    if not isinstance(source_filename, str):
        return ""
    normalized_filename = source_filename.strip()
    if normalized_filename not in get_guest_disguise_avatar_filenames():
        return ""
    return ensure_guest_avatar_asset(normalized_filename)


def prepare_guest_avatar_asset(
    *,
    archetype: str | None,
    gender: str | None,
    rng: random.Random,
) -> str:
    """按门客的文/武和性别选择并准备头像资源。"""
    source_filename = pick_guest_avatar_filename(
        get_guest_avatar_catalog(),
        archetype,
        gender,
        rng=rng,
        fallback_to_any=False,
    )
    if not source_filename:
        return ""
    return ensure_guest_avatar_asset(source_filename)


def _resolve_guest_avatar_asset_path(guest: object) -> str:
    raw_asset_path = getattr(guest, "avatar_asset_key", "")
    if not isinstance(raw_asset_path, str):
        return ""
    asset_path = raw_asset_path.strip()
    if not asset_path.startswith(GUEST_AVATAR_STORAGE_PREFIX):
        return ""
    if Path(asset_path).name != asset_path.removeprefix(GUEST_AVATAR_STORAGE_PREFIX):
        return ""
    try:
        return asset_path if default_storage.exists(asset_path) else ""
    except (OSError, ValueError):
        return ""


def has_guest_avatar_override(guest: object) -> bool:
    """Return whether a guest has a supported runtime avatar override or selection."""
    override_key = getattr(guest, "avatar_override_key", "")
    if isinstance(override_key, str) and override_key.strip() == SUPER_GUEST_DISGUISE_AVATAR_KEY:
        return True
    return bool(_resolve_guest_avatar_asset_path(guest))


def resolve_guest_avatar_url(guest: object) -> str:
    """Resolve a guest's effective avatar URL without exposing arbitrary storage paths."""
    if has_guest_avatar_override(guest):
        override_key = getattr(guest, "avatar_override_key", "")
        if isinstance(override_key, str) and override_key.strip() == SUPER_GUEST_DISGUISE_AVATAR_KEY:
            return default_storage.url(SUPER_GUEST_DISGUISE_AVATAR_PATH)

        asset_path = _resolve_guest_avatar_asset_path(guest)
        if asset_path:
            try:
                return str(default_storage.url(asset_path) or "")
            except (AttributeError, ValueError):
                return ""

    avatar = getattr(getattr(guest, "template", None), "avatar", None)
    if not avatar:
        return ""
    try:
        return str(avatar.url or "")
    except (AttributeError, ValueError):
        return ""
