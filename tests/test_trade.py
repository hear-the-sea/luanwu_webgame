"""
Compatibility entrypoint for trade service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/trade_service/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.trade_service.market_cache import *  # noqa: F401,F403
from tests.trade_service.market_cancel import *  # noqa: F401,F403
from tests.trade_service.market_expire import *  # noqa: F401,F403
from tests.trade_service.market_listing import *  # noqa: F401,F403
from tests.trade_service.market_purchase import *  # noqa: F401,F403
from tests.trade_service.market_queries import *  # noqa: F401,F403
