"""
Compatibility entrypoint for gameplay task tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/gameplay_tasks/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.gameplay_tasks.guest_tasks import *  # noqa: F401,F403
from tests.gameplay_tasks.mission_building_work import *  # noqa: F401,F403
from tests.gameplay_tasks.production_technology import *  # noqa: F401,F403
from tests.gameplay_tasks.pvp_tasks import *  # noqa: F401,F403
from tests.gameplay_tasks.scheduled_policy import *  # noqa: F401,F403
