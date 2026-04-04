"""
Compatibility entrypoint for guild task tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guilds_tasks/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.guilds_tasks.maintenance_tasks import *  # noqa: F401,F403
from tests.guilds_tasks.mission_tasks import *  # noqa: F401,F403
from tests.guilds_tasks.production_tasks import *  # noqa: F401,F403
