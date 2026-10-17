"""Isolated local preview; never connects to the configured game database."""

import os

from . import *  # noqa: F401, F403
from .base import BASE_DIR

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(BASE_DIR / ".local_preview.sqlite3")}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
# Celery gives these process environment variables precedence over settings.
os.environ["CELERY_BROKER_URL"] = CELERY_BROKER_URL
os.environ["CELERY_RESULT_BACKEND"] = CELERY_RESULT_BACKEND
os.environ.pop("CELERY_BROKER_READ_URL", None)
os.environ.pop("CELERY_BROKER_WRITE_URL", None)
CELERY_TASK_ALWAYS_EAGER = False
HEALTH_CHECK_CHANNEL_LAYER = False
HEALTH_CHECK_CELERY_BROKER = False
HEALTH_CHECK_CELERY_WORKERS = False
HEALTH_CHECK_CELERY_BEAT = False
HEALTH_CHECK_CELERY_ROUNDTRIP = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = BASE_DIR / ".local_preview_media"  # noqa: F405
ROOT_URLCONF = "config.local_preview_urls"
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
