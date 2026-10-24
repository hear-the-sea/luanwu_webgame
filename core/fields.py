"""Image fields that normalize newly stored uploads to WebP."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from django.core.files.base import File
from django.db.models.fields.files import ImageField, ImageFieldFile

from core.utils.image_utils import convert_image_content_to_webp


class WebPImageFieldFile(ImageFieldFile):
    """Convert uploads made through forms and ``FieldFile.save()`` before storage."""

    def save(self, name: str, content: File, save: bool = True) -> None:
        field = cast(WebPImageField, self.field)
        converted_content, converted_name = convert_image_content_to_webp(
            content,
            name,
            max_size=field.max_size,
            quality=field.quality,
        )
        super().save(converted_name, converted_content, save=save)


class WebPImageField(ImageField):
    """An ImageField that stores accepted image formats as bounded WebP files."""

    attr_class = WebPImageFieldFile

    def __init__(
        self,
        *args: Any,
        max_size: tuple[int, int] | None = None,
        quality: int = 85,
        **kwargs: Any,
    ) -> None:
        if max_size is not None and (len(max_size) != 2 or any(size <= 0 for size in max_size)):
            raise ValueError("max_size must contain two positive dimensions")
        if not 1 <= quality <= 100:
            raise ValueError("quality must be between 1 and 100")
        self.max_size = max_size
        self.quality = quality
        super().__init__(*args, **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        name, path, args, kwargs = super().deconstruct()
        if self.max_size is not None:
            kwargs["max_size"] = self.max_size
        if self.quality != 85:
            kwargs["quality"] = self.quality
        return name, path, args, kwargs
