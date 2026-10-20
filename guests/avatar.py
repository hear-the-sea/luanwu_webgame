"""门客头像解析及按文武/性别选择头像资源。"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import cast

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.paginator import Paginator

from core.utils.image_utils import compress_and_resize_image

from .constants import GUEST_DISGUISE_AVATAR_PREFIXES, SUPER_GUEST_DISGUISE_AVATAR_KEY, SUPER_GUEST_DISGUISE_AVATAR_PATH

logger = logging.getLogger(__name__)

GUEST_AVATAR_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
GUEST_AVATAR_STORAGE_PREFIX = "guests/"
CUSTOM_GUEST_AVATAR_DIRECTORY = Path("monkey") / "其他随机角色"
CUSTOM_GUEST_PIG_AVATAR_DIRECTORY = Path("pig")
CUSTOM_GUEST_AVATAR_PAGE_SIZE = 16
CUSTOM_GUEST_AVATAR_GROUP_LABELS = {
    "original": "乱舞原版形象",
    "monkey": "拓展悠嘻猴形象",
    "pig": "拓展Mr.Pig形象",
}
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


def get_guest_avatar_asset_name(source_filename: str) -> str:
    """保持嵌套素材名称独立，并使用现有头像解析支持的单层存储路径。"""
    asset_stem = "__".join(Path(source_filename).with_suffix("").parts)
    return f"{GUEST_AVATAR_STORAGE_PREFIX}{asset_stem}.webp"


def ensure_guest_avatar_asset(source_filename: str) -> str:
    """将头像源图压缩到媒体目录并返回受控的存储路径。"""
    source_dir = Path(settings.BASE_DIR) / "data" / "images" / "guests"
    source_path = source_dir / source_filename
    relative_path = Path(source_filename)
    if relative_path.parent not in {Path("."), CUSTOM_GUEST_AVATAR_DIRECTORY, CUSTOM_GUEST_PIG_AVATAR_DIRECTORY}:
        return ""
    if not source_path.is_file() or not source_path.resolve().is_relative_to(source_dir.resolve()):
        return ""

    target_name = get_guest_avatar_asset_name(source_filename)
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
    return _build_guest_avatar_options(get_guest_disguise_avatar_filenames())


def _custom_guest_directory_filenames(directory: Path) -> tuple[str, ...]:
    source_dir = Path(settings.BASE_DIR) / "data" / "images" / "guests" / directory
    if not source_dir.is_dir():
        return ()
    return tuple(
        sorted(
            (directory / path.name).as_posix()
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in GUEST_AVATAR_EXTENSIONS
        )
    )


def get_custom_guest_avatar_catalog() -> dict[str, tuple[str, ...]]:
    """仅列出各栏源文件，展开前不压缩图片或访问媒体存储。"""
    return {
        "original": get_guest_disguise_avatar_filenames(),
        "monkey": _custom_guest_directory_filenames(CUSTOM_GUEST_AVATAR_DIRECTORY),
        "pig": _custom_guest_directory_filenames(CUSTOM_GUEST_PIG_AVATAR_DIRECTORY),
    }


def get_custom_guest_avatar_filenames() -> tuple[str, ...]:
    """自定义门客可使用的全部受控素材。"""
    return tuple(filename for filenames in get_custom_guest_avatar_catalog().values() for filename in filenames)


def get_custom_guest_avatar_options(group_key: str | None = None) -> list[dict[str, str]]:
    """准备指定栏目的形象，未指定时保留完整列表调用方式。"""
    filenames = (
        get_custom_guest_avatar_catalog().get(group_key, ())
        if group_key is not None
        else get_custom_guest_avatar_filenames()
    )
    return _build_guest_avatar_options(filenames)


def get_custom_guest_avatar_page(group_key: str, page_number: str | None) -> dict:
    """先对素材分页，再处理当前页头像，避免翻页时加载整栏图片。"""
    filenames = get_custom_guest_avatar_catalog().get(group_key, ())
    page = Paginator(filenames, CUSTOM_GUEST_AVATAR_PAGE_SIZE).get_page(page_number)
    return {
        "avatars": _build_guest_avatar_options(cast(Sequence[str], page.object_list)),
        "page": page.number,
        "pages": page.paginator.num_pages,
        "total": page.paginator.count,
    }


def get_custom_guest_avatar_option(source_filename: str) -> dict[str, str] | None:
    """确认预览只处理玩家已选中的一张图片。"""
    if source_filename not in get_custom_guest_avatar_filenames():
        return None
    options = _build_guest_avatar_options((source_filename,))
    return options[0] if options else None


def _build_guest_avatar_options(filenames: Sequence[str]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for filename in filenames:
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


def resolve_custom_guest_avatar_asset(source_filename: object) -> str:
    """仅接受自定义门客形象列表中的素材。"""
    if not isinstance(source_filename, str):
        return ""
    normalized_filename = source_filename.strip()
    if normalized_filename not in get_custom_guest_avatar_filenames():
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


def _resolve_guest_avatar_asset_path(guest: object, field_name: str = "avatar_asset_key") -> str:
    raw_asset_path = getattr(guest, field_name, "")
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
    if getattr(guest, "use_original_avatar", False) is True:
        original_path = _resolve_guest_avatar_asset_path(guest, "original_avatar_asset_key")
        if original_path:
            try:
                return str(default_storage.url(original_path) or "")
            except (AttributeError, ValueError):
                pass
    elif has_guest_avatar_override(guest):
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
