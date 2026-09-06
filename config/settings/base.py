"""
Base Django settings - core configuration.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")

RUNNING_TESTS = ("pytest" in sys.modules) or ("test" in sys.argv) or ("pytest" in os.path.basename(sys.argv[0] or ""))


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def env_float(key: str, default: float) -> float:
    raw_value = env(key, str(default))
    try:
        parsed = float(raw_value or default)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed):
        return float(default)
    return parsed


def env_json_string_mapping(key: str) -> dict[str, str]:
    raw_value = env(key, "{}")
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a JSON object") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(item_key, str) or not isinstance(item_value, str) for item_key, item_value in parsed.items()
    ):
        raise ValueError(f"{key} must map string key IDs to string secrets")
    return parsed


def _production_default_flag(*, debug: bool, running_tests: bool) -> str:
    return "0" if debug or running_tests else "1"


# Game time multiplier
# Tests must not inherit the accelerated local development clock from `.env`.
GAME_TIME_MULTIPLIER = 1.0 if RUNNING_TESTS else env_float("GAME_TIME_MULTIPLIER", 1.0)
if not math.isfinite(GAME_TIME_MULTIPLIER) or GAME_TIME_MULTIPLIER <= 0:
    GAME_TIME_MULTIPLIER = 1.0

# DEBUG should default to False for security
DEBUG = env("DJANGO_DEBUG", "0") == "1"

# Battle debugger is a development-only tool and should be explicitly enabled.
ENABLE_BATTLE_DEBUGGER = DEBUG and env("DJANGO_ENABLE_DEBUGGER", "0") == "1"

# Gate D2 stays fail-closed unless an out-of-band generator attestation key is set.
VIRTUAL_PLAYER_GATE_D2_ATTESTATION_KEYS = env_json_string_mapping("DJANGO_VIRTUAL_PLAYER_GATE_D2_ATTESTATION_KEYS_JSON")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "channels",
    "accounts",
    "gameplay",
    "guests",
    "battle",
    "trade",
    "guilds",
    "battle_debugger" if ENABLE_BATTLE_DEBUGGER else None,
]
INSTALLED_APPS = [app for app in INSTALLED_APPS if app]

MIDDLEWARE = [
    "core.middleware.request_id.RequestIDMiddleware",
    "core.middleware.access_log.AccessLogMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.single_session.SingleSessionMiddleware",
    "core.middleware.online_presence.OnlinePresenceMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "gameplay.context_processors.notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_FINDERS = [
    "config.static_finders.CatalogImageFinder",
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Default storage backends.
# In production (DEBUG=0), static assets can use manifest hashing to enable immutable caching.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

if not DEBUG and env("DJANGO_STATIC_USE_MANIFEST", "1") == "1" and not RUNNING_TESTS:
    STORAGES["staticfiles"] = {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    }

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "accounts:login"

EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = env("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = max(1, int(env("EMAIL_TIMEOUT", "10")))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "webgame@example.com")
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST else "django.core.mail.backends.console.EmailBackend",
)

# Registration verification emails are budgeted before delivery so concurrent
# requests cannot reserve more than the configured monthly provider quota.
EMAIL_MONTHLY_SEND_LIMIT = max(0, int(env("EMAIL_MONTHLY_SEND_LIMIT", "3000")))
EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS = max(
    60,
    int(env("EMAIL_VERIFICATION_TOKEN_MAX_AGE_SECONDS", "86400")),
)
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = max(
    1,
    int(env("EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", "60")),
)
EMAIL_VERIFICATION_RESEND_IP_LIMIT = max(
    1,
    int(env("EMAIL_VERIFICATION_RESEND_IP_LIMIT", "5")),
)
EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS = max(
    1,
    int(env("EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS", "3600")),
)

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
        "recruit": "20/hour",
        "battle": "100/hour",
        "claim": "50/hour",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "春秋乱世庄园主 API",
    "DESCRIPTION": "Django 游戏项目 API 文档",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/",
}

ENABLE_API_DOCS = env("DJANGO_ENABLE_API_DOCS", "1" if DEBUG else "0") == "1"
API_DOCS_REQUIRE_AUTH = env("DJANGO_API_DOCS_REQUIRE_AUTH", "0" if DEBUG else "1") == "1"

# Trusted reverse proxy addresses (exact IPs or CIDR), comma-separated.
trusted_proxy_ips_str = env("DJANGO_TRUSTED_PROXY_IPS", "")
TRUSTED_PROXY_IPS = [ip.strip() for ip in trusted_proxy_ips_str.split(",") if ip.strip()]

ACCESS_LOG_ENABLED = env("DJANGO_ACCESS_LOG", "1") == "1"
ACCESS_LOG_TRUST_PROXY = env("DJANGO_ACCESS_LOG_TRUST_PROXY", "0") == "1"
if ACCESS_LOG_TRUST_PROXY and not TRUSTED_PROXY_IPS:
    ACCESS_LOG_TRUST_PROXY = False

# Minimum intervals for resource sync and manor state refresh
RESOURCE_SYNC_MIN_INTERVAL_SECONDS = int(env("DJANGO_RESOURCE_SYNC_MIN_INTERVAL_SECONDS", "1" if DEBUG else "5"))
RESOURCE_SYNC_TRANSACTION_BATCH_SIZE = int(env("DJANGO_RESOURCE_SYNC_TRANSACTION_BATCH_SIZE", "50"))
MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = int(
    env("DJANGO_MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS", "1" if DEBUG else "5")
)
# Arena limits
ARENA_DAILY_PARTICIPATION_LIMIT = int(env("DJANGO_ARENA_DAILY_PARTICIPATION_LIMIT", "5"))
ARENA_TOURNAMENT_PLAYER_LIMIT = int(env("DJANGO_ARENA_TOURNAMENT_PLAYER_LIMIT", "10"))
ARENA_SHORTAGE_COLD_START_MAX_REAL_ENTRIES = max(
    0,
    int(env("DJANGO_ARENA_SHORTAGE_COLD_START_MAX_REAL_ENTRIES", "2")),
)
# Runtime baseline bootstrap stays bounded so a persistently unhealthy Arena
# cannot teach the safety gate a bad baseline.
ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MIN_MATURE_WINDOWS = max(
    2,
    min(24, int(env("DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MIN_MATURE_WINDOWS", "3"))),
)
ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MAX_RATIO = min(
    1.0,
    max(0.0, env_float("DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_MAX_RATIO", 0.25)),
)
# A new server can have a stable, legitimate shortage ratio well above the
# mature-population cap. Permit that bounded early-game case only when the
# observed real population is still small and the reserve has demonstrated
# healthy supply across repeated windows.
ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_REAL_ENTRIES = max(
    ARENA_SHORTAGE_COLD_START_MAX_REAL_ENTRIES,
    min(1000, int(env("DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_REAL_ENTRIES", "10"))),
)
ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_RATIO = min(
    1.0,
    max(0.0, env_float("DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_EARLY_GAME_MAX_RATIO", 1.0)),
)
ARENA_SHORTAGE_BASELINE_BOOTSTRAP_TTL_HOURS = max(
    1,
    min(24 * 30, int(env("DJANGO_ARENA_SHORTAGE_BASELINE_BOOTSTRAP_TTL_HOURS", "168"))),
)
VIRTUAL_PLAYER_SAFETY_AUTO_RESUME_CLEAN_WINDOWS = max(
    1,
    int(env("DJANGO_VIRTUAL_PLAYER_SAFETY_AUTO_RESUME_CLEAN_WINDOWS", "3")),
)
VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD = max(
    1,
    int(env("DJANGO_VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD", "3")),
)
VIRTUAL_PLAYER_HEALTH_COOLDOWN_SECONDS = max(
    60,
    int(env("DJANGO_VIRTUAL_PLAYER_HEALTH_COOLDOWN_SECONDS", "1800")),
)
VIRTUAL_PLAYER_HEALTH_RECOVERY_PROBE_SECONDS = max(
    30,
    int(env("DJANGO_VIRTUAL_PLAYER_HEALTH_RECOVERY_PROBE_SECONDS", "60")),
)
VIRTUAL_PLAYER_HEALTH_RECOVERY_SUCCESS_THRESHOLD = max(
    1,
    int(env("DJANGO_VIRTUAL_PLAYER_HEALTH_RECOVERY_SUCCESS_THRESHOLD", "2")),
)

# Cache TTL for home/dashboard stats
HOME_STATS_CACHE_TTL_SECONDS = int(env("DJANGO_HOME_STATS_CACHE_TTL_SECONDS", "15"))
# Bounded wait for another worker to fill the market stats cache. Keep this
# independently tunable because increasing it trades duplicate queries for tail latency.
MARKET_STATS_CACHE_LOCK_WAIT_SECONDS = max(
    0.0,
    env_float("DJANGO_MARKET_STATS_CACHE_LOCK_WAIT_SECONDS", 0.2),
)
# Cache TTL for defender 24h raid-received counter in attack checks
RAID_RECENT_ATTACKS_CACHE_TTL_SECONDS = int(env("DJANGO_RAID_RECENT_ATTACKS_CACHE_TTL_SECONDS", "5"))
# Raid capture rate (0.0 ~ 1.0, clamped in gameplay.constants.get_raid_capture_guest_rate)
RAID_CAPTURE_GUEST_RATE = env_float("DJANGO_RAID_CAPTURE_GUEST_RATE", 0.5)

# High-value thresholds for logging/monitoring
TRADE_HIGH_VALUE_SILVER_THRESHOLD = int(env("DJANGO_TRADE_HIGH_VALUE_SILVER_THRESHOLD", "1000000"))
AUCTION_HIGH_BID_THRESHOLD = int(env("DJANGO_AUCTION_HIGH_BID_THRESHOLD", "200"))

HEALTH_CHECK_REQUIRE_INTERNAL = (
    env(
        "DJANGO_HEALTH_CHECK_REQUIRE_INTERNAL",
        _production_default_flag(debug=DEBUG, running_tests=RUNNING_TESTS),
    )
    == "1"
)
HEALTH_CHECK_CHANNEL_LAYER = (
    env(
        "DJANGO_HEALTH_CHECK_CHANNEL_LAYER",
        _production_default_flag(debug=DEBUG, running_tests=RUNNING_TESTS),
    )
    == "1"
)
HEALTH_CHECK_CHANNEL_LAYER_TIMEOUT_SECONDS = env_float("DJANGO_HEALTH_CHECK_CHANNEL_LAYER_TIMEOUT_SECONDS", 1.0)
HEALTH_CHECK_CACHE_TTL_SECONDS = int(env("DJANGO_HEALTH_CHECK_CACHE_TTL_SECONDS", "0" if DEBUG else "3"))
HEALTH_CHECK_INCLUDE_DETAILS = env("DJANGO_HEALTH_CHECK_INCLUDE_DETAILS", "1" if DEBUG else "0") == "1"
HEALTH_CHECK_CELERY_BROKER = (
    env(
        "DJANGO_HEALTH_CHECK_CELERY_BROKER",
        _production_default_flag(debug=DEBUG, running_tests=RUNNING_TESTS),
    )
    == "1"
)
# Authentication enforcement should fail closed by default; environments that
# need a softer posture must opt in explicitly.
SINGLE_SESSION_FAIL_OPEN = env("DJANGO_SINGLE_SESSION_FAIL_OPEN", "0") == "1"

# Bound authenticated WebSocket fan-out per user. Slots expire unless the
# consumer heartbeat renews them, so unclean worker exits cannot leak capacity.
WEBSOCKET_MAX_CONNECTIONS_PER_USER = max(1, int(env("DJANGO_WEBSOCKET_MAX_CONNECTIONS_PER_USER", "9")))
WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS = max(
    6,
    int(env("DJANGO_WEBSOCKET_CONNECTION_SLOT_TTL_SECONDS", "30")),
)
WEBSOCKET_WORKER_LEASE_TTL_SECONDS = max(
    4,
    int(env("DJANGO_WEBSOCKET_WORKER_LEASE_TTL_SECONDS", "8")),
)
WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS = max(
    1,
    int(env("DJANGO_WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS", "2")),
)
if WEBSOCKET_WORKER_LEASE_HEARTBEAT_SECONDS * 2 >= WEBSOCKET_WORKER_LEASE_TTL_SECONDS:
    raise RuntimeError("WebSocket worker lease heartbeat must be less than half the TTL")

# Proxy-independent per-IP WebSocket protection. Caddy supplies the client IP
# only across the trusted Docker network; Redis keeps limits consistent across workers.
WEBSOCKET_MAX_CONNECTIONS_PER_IP = max(1, int(env("DJANGO_WEBSOCKET_MAX_CONNECTIONS_PER_IP", "20")))
WEBSOCKET_HANDSHAKE_RATE_PER_SECOND = max(1, int(env("DJANGO_WEBSOCKET_HANDSHAKE_RATE_PER_SECOND", "10")))
WEBSOCKET_HANDSHAKE_BURST = max(1, int(env("DJANGO_WEBSOCKET_HANDSHAKE_BURST", "20")))
WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS = max(
    30,
    int(env("DJANGO_WEBSOCKET_IP_CONNECTION_SLOT_TTL_SECONDS", "120")),
)
