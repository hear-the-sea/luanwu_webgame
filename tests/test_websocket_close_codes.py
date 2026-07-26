from unittest.mock import Mock

import pytest
from daphne.ws_protocol import WebSocketProtocol as DaphneWebSocketProtocol

from websocket.close_codes import APPLICATION_CLOSE_CODES, SERVICE_UNAVAILABLE_CLOSE_CODE


def test_service_unavailable_close_code_uses_private_application_range():
    assert SERVICE_UNAVAILABLE_CLOSE_CODE == 4503
    assert 4000 <= SERVICE_UNAVAILABLE_CLOSE_CODE <= 4999


@pytest.mark.parametrize("close_code", APPLICATION_CLOSE_CODES)
def test_application_close_codes_are_accepted_by_daphne_autobahn(close_code):
    protocol = object.__new__(DaphneWebSocketProtocol)
    protocol.sendCloseFrame = Mock()

    protocol.serverClose(code=close_code)

    protocol.sendCloseFrame.assert_called_once_with(code=close_code, reasonUtf8=None, isReply=False)


def test_service_unavailable_close_is_safe_after_protocol_already_closed():
    protocol = object.__new__(DaphneWebSocketProtocol)
    protocol.state = DaphneWebSocketProtocol.STATE_CLOSED
    protocol.log = Mock()

    protocol.serverClose(code=SERVICE_UNAVAILABLE_CLOSE_CODE)

    protocol.log.debug.assert_called_once_with("ignoring sendCloseFrame since connection already closed")
