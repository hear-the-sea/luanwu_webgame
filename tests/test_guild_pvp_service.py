"""
Compatibility entrypoint for guild PVP service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guild_pvp_service/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.guild_pvp_service.battle_flow import *  # noqa: F401,F403
from tests.guild_pvp_service.start_flow import *  # noqa: F401,F403
