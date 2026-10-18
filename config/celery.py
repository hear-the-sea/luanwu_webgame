from __future__ import annotations

import logging
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY", force=True)


def _normalize_celery_redis_environment() -> None:
    """Keep Celery's environment-first URL overrides aligned with Django settings."""
    from django.conf import settings

    from config.settings.database import _redis_url_with_password

    redis_password = settings.REDIS_PASSWORD
    if not redis_password:
        return

    for name in (
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "CELERY_BROKER_READ_URL",
        "CELERY_BROKER_WRITE_URL",
    ):
        url = os.environ.get(name)
        if url:
            os.environ[name] = _redis_url_with_password(url, redis_password)


# Celery reads these variables before its Django settings mapping. Normalize
# them here so a separate REDIS_PASSWORD is not bypassed by a raw URL override.
_normalize_celery_redis_environment()

app.autodiscover_tasks()

# `core` is not a Django app, so its shared tasks need explicit import for registration.
import core.tasks  # noqa: F401,E402
from core.celery_signals import connect_task_signals  # noqa: E402
from core.worker_liveness import register_worker_supervision  # noqa: E402

connect_task_signals()
register_worker_supervision(app)

logger = logging.getLogger(__name__)


@app.task(bind=True)
def debug_task(self):
    # 安全优化：只记录必要的调试信息，避免打印完整请求对象（可能包含敏感参数）
    logger.debug("Debug task executed: id=%s, task=%s", self.request.id, self.request.task)
