from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import logging  # noqa: E402
import warnings  # noqa: E402

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402
from django.core.exceptions import AppRegistryNotReady  # noqa: E402

from websocket.middleware.ip_capacity import WebSocketIPCapacityMiddleware  # noqa: E402
from websocket.routing_status import set_websocket_routing_status  # noqa: E402

logger = logging.getLogger(__name__)

# Initialize Django ASGI application early to ensure app registry is ready
django_asgi_app = get_asgi_application()

# Import websocket routing only after Django ASGI initialization is ready.
try:
    import websocket.routing as websocket_routing
except (ImportError, AppRegistryNotReady, OSError, RuntimeError) as exc:  # pragma: no cover
    # Keep DEBUG mode usable when websocket routing is temporarily unavailable
    # during local startup, but do not hide unrelated programming errors.
    if settings.DEBUG:
        warnings.warn(f"WebSocket routing disabled: {exc}", RuntimeWarning, stacklevel=2)
        logger.exception("Failed to import websocket routing in DEBUG mode; WebSocket endpoints disabled: %s", exc)
        set_websocket_routing_status(ok=False, error=str(exc))
        websocket_routing = None  # type: ignore[assignment]
    else:
        logger.exception("Failed to import websocket routing; refusing to start in production: %s", exc)
        raise
else:
    set_websocket_routing_status(ok=True)

websocket_urlpatterns = []
if websocket_routing and getattr(websocket_routing, "websocket_urlpatterns", None):
    websocket_urlpatterns.extend(websocket_routing.websocket_urlpatterns)

# HTTP Application Configuration
# -------------------------------
# In production, static files should be served by a dedicated web server (Caddy, CDN)
# or using WhiteNoise middleware. ASGIStaticFilesHandler is only for development.
http_app = django_asgi_app
if settings.DEBUG:
    # Development mode: serve static files via ASGI for convenience
    http_app = ASGIStaticFilesHandler(django_asgi_app)

# WebSocket Security
# ------------------
# AllowedHostsOriginValidator prevents cross-origin WebSocket hijacking attacks
# by validating the Origin header against ALLOWED_HOSTS setting
application = ProtocolTypeRouter(
    {
        "http": http_app,
        "websocket": AllowedHostsOriginValidator(
            WebSocketIPCapacityMiddleware(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)))
        ),
    }
)
