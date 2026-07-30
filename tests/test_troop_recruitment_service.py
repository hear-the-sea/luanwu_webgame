"""
Compatibility entrypoint for troop recruitment service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/troop_recruitment_service/` submodules while this path remains stable
for existing pytest commands and CI references.
"""

from tests.troop_recruitment_service.finalization import *  # noqa: F401,F403
from tests.troop_recruitment_service.scheduling import *  # noqa: F401,F403
from tests.troop_recruitment_service.start_flow import *  # noqa: F401,F403
from tests.troop_recruitment_service.synchronous import *  # noqa: F401,F403
