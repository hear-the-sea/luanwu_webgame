"""
Compatibility entrypoint for guild warehouse service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guild_warehouse_service/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.guild_warehouse_service.exchange_flow import *  # noqa: F401,F403
from tests.guild_warehouse_service.migration_roundtrip import *  # noqa: F401,F403
from tests.guild_warehouse_service.production_flow import *  # noqa: F401,F403
from tests.guild_warehouse_service.resource_projection import *  # noqa: F401,F403
