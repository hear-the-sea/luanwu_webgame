"""Static files backed by a small, explicit subset of the data catalog."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.contrib.staticfiles.finders import BaseFinder
from django.contrib.staticfiles.utils import matches_patterns
from django.core.checks import Error
from django.core.files.storage import FileSystemStorage


class _CatalogImageStorage(FileSystemStorage):
    """collectstatic 使用公开名称读取时仍定位实际的目录文件。"""

    def __init__(self, root, assets):
        self.assets = assets
        super().__init__(location=root)

    def path(self, name):
        return super().path(self.assets.get(name, name))


class CatalogImageFinder(BaseFinder):
    """Expose only catalog images that are referenced directly by templates.

    ``data/images`` remains the source of truth for management commands, but it
    is not a static directory. Keeping the allowlist here prevents collectstatic
    from walking and copying the full raw-image catalog.
    """

    ASSETS: ClassVar[dict[str, str]] = {
        "buildings/guild-residence.webp": "buildings/guild-residence.webp",
        "items/chunqiu_coin.png": "items/chunqiu_coin.png",
        "items/guild_silver_boost_card.png": "items/guild_silver_boost_card.png",
        "items/large.png": "items/large.png",
    }
    # 仅发布实际游戏图层，不发布素材校样、提示词与源文件。
    ASSETS.update(
        {
            f"season-assets/{region}/{name}.png": f"pvp/layers-v1/{region}/{name}.png"
            for region, names in {
                "mountain": ("background", "inn", "gate", "ruins"),
                "river": ("background", "town", "market", "warehouse", "docks"),
                "forest": ("background", "camp", "granary", "lodge"),
            }.items()
            for name in names
        }
    )

    ASSETS.update(
        {
            f"season-troops/{name}.png": f"troops/{name}.png"
            for name in ("daosheng", "qiangwang", "jiansheng", "quansheng", "jianshen", "shixuetieqi")
        }
    )

    def __init__(self, *args, **kwargs):
        self.root = Path(settings.BASE_DIR) / "data" / "images"
        self.storage = _CatalogImageStorage(self.root, self.ASSETS)
        super().__init__(*args, **kwargs)

    def check(self, **kwargs):
        errors = []
        if not self.root.is_dir():
            return [
                Error(
                    f"The catalog image directory '{self.root}' does not exist.",
                    id="webgame.staticfiles.E001",
                )
            ]

        for public_path, source_path in self.ASSETS.items():
            if not (self.root / source_path).is_file():
                errors.append(
                    Error(
                        f"Catalog image '{source_path}' for '{public_path}' is missing.",
                        id="webgame.staticfiles.E002",
                    )
                )
        return errors

    def _find_source(self, path: str) -> Path | None:
        source_path = self.ASSETS.get(path)
        if source_path is None:
            return None
        candidate = self.root / source_path
        return candidate if candidate.is_file() else None

    def find(self, path, find_all=False, **kwargs):
        if kwargs:
            find_all = self._check_deprecated_find_param(find_all=find_all, **kwargs)
        source_path = self._find_source(path)
        if source_path is None:
            # Django's aggregate finder expects an iterable for a miss; a
            # ``None`` result would be wrapped as ``[None]`` by finders.find().
            return []
        return [str(source_path)] if find_all else str(source_path)

    def list(self, ignore_patterns):
        for public_path, source_path in self.ASSETS.items():
            if matches_patterns(public_path, ignore_patterns):
                continue
            if self._find_source(public_path) is not None:
                yield public_path, self.storage
