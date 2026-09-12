import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

from .models import GuestTemplate

logger = logging.getLogger(__name__)


@receiver([post_save, post_delete], sender=GuestTemplate)
def clear_guest_template_cache(sender, **kwargs):
    """
    GuestTemplate 变更时清理招募模板缓存，避免运行中使用陈旧数据。
    """
    if kwargs.get("raw"):  # 跳过数据迁移
        return

    try:
        from .services.recruitment_templates import clear_template_cache

        clear_template_cache()
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        # Best-effort: cache clear should never break writes.
        logger.warning("Failed to clear guest template cache: %s", exc, exc_info=True)
