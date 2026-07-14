"""
Security configuration - CSRF, CORS, SSL, HSTS, etc.
"""

from __future__ import annotations

import os

from .base import DEBUG, env

# Security: SECRET_KEY must be set via environment variable in production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set in environment for production. "
            "Generate one with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'"
        )
    import warnings

    from django.core.management.utils import get_random_secret_key

    SECRET_KEY = get_random_secret_key()
    warnings.warn(
        "DJANGO_SECRET_KEY not set. Using temporary random key for development only. "
        "Run 'python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"' to generate one.",
        RuntimeWarning,
    )

if not DEBUG and (
    SECRET_KEY == "please-change-me-in-production"
    or len(SECRET_KEY) < 50
    or len(set(SECRET_KEY)) < 5
    or SECRET_KEY.startswith("django-insecure-")
):
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be at least 50 characters, contain at least 5 unique characters, "
        "and must not use public placeholder or django-insecure- values in production."
    )

# ALLOWED_HOSTS configuration
allowed_hosts_str = env("DJANGO_ALLOWED_HOSTS", "")
ALLOWED_HOSTS = []
if allowed_hosts_str:
    ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_str.split(",") if host.strip() and host.strip() != "*"]

if not DEBUG and not ALLOWED_HOSTS:
    raise RuntimeError(
        "ALLOWED_HOSTS must be set in production environment. "
        "Set DJANGO_ALLOWED_HOSTS in your environment with comma-separated domains."
    )

if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Session Security
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# CSRF Security
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False  # Allow frontend JavaScript to read CSRF token
CSRF_COOKIE_SAMESITE = "Lax"

csrf_origins_str = env("DJANGO_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_origins_str.split(",") if origin.strip()]

# SSL/HTTPS Configuration
SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT", "1" if not DEBUG else "0") == "1"

SECURE_PROXY_SSL_HEADER: tuple[str, str] | None
if env("DJANGO_USE_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SECURE_PROXY_SSL_HEADER = None

# HSTS Configuration
SECURE_HSTS_SECONDS = int(env("DJANGO_SECURE_HSTS_SECONDS", "31536000" if not DEBUG else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

# Content Security
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

# File Upload Limits
FILE_UPLOAD_MAX_MEMORY_SIZE = int(env("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(env("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))

# Referrer Policy
SECURE_REFERRER_POLICY = "same-origin"

# CORS Configuration
CORS_ALLOW_CREDENTIALS = True

cors_allowed_origins_str = env("DJANGO_CORS_ALLOWED_ORIGINS", "")
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in cors_allowed_origins_str.split(",") if origin.strip()]

if DEBUG and not CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "hx-request",
    "hx-current-url",
    "hx-target",
    "hx-trigger",
]

CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
