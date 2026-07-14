"""Exceptions shared by websocket transport and world chat services."""


class WorldChatInfrastructureError(RuntimeError):
    """Expected infrastructure failure while processing world chat."""


class WebSocketConnectionLimitUnavailable(RuntimeError):
    """Connection capacity cannot be checked against Redis."""
