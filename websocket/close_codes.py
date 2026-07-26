"""Application-defined WebSocket close codes.

Daphne delegates application-initiated closes to Autobahn, whose public
``sendClose`` API accepts only code 1000 or application codes in 3000-4999.
Keep every server-sent code in the private-use range so the ASGI contract also
works at the concrete server boundary.
"""

AUTHENTICATION_REQUIRED_CLOSE_CODE = 4401
INVALID_SESSION_CLOSE_CODE = 4403
CONNECTION_LIMIT_REACHED_CLOSE_CODE = 4429
SERVICE_UNAVAILABLE_CLOSE_CODE = 4503

APPLICATION_CLOSE_CODES = (
    AUTHENTICATION_REQUIRED_CLOSE_CODE,
    INVALID_SESSION_CLOSE_CODE,
    CONNECTION_LIMIT_REACHED_CLOSE_CODE,
    SERVICE_UNAVAILABLE_CLOSE_CODE,
)

__all__ = [
    "APPLICATION_CLOSE_CODES",
    "AUTHENTICATION_REQUIRED_CLOSE_CODE",
    "CONNECTION_LIMIT_REACHED_CLOSE_CODE",
    "INVALID_SESSION_CLOSE_CODE",
    "SERVICE_UNAVAILABLE_CLOSE_CODE",
]
