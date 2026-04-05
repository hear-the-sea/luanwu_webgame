"""
Compatibility entrypoint for guild mission service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guild_mission_service/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.guild_mission_service.finalize_flow import *  # noqa: F401,F403
from tests.guild_mission_service.launch_flow import *  # noqa: F401,F403
from tests.guild_mission_service.model_constraints import *  # noqa: F401,F403
from tests.guild_mission_service.retreat_flow import *  # noqa: F401,F403
