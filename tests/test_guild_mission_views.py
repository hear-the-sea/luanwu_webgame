"""
Compatibility entrypoint for guild mission view tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guild_mission_views/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.guild_mission_views.action_cases import *  # noqa: F401,F403
from tests.guild_mission_views.page_cases import *  # noqa: F401,F403
from tests.guild_mission_views.permission_cases import *  # noqa: F401,F403
from tests.guild_mission_views.support import guild_member_client  # noqa: F401
