"""
Compatibility entrypoint for arena service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/arena_services/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.arena_services.cleanup import *  # noqa: F401,F403
from tests.arena_services.coop_registration import *  # noqa: F401,F403
from tests.arena_services.coop_resolution import *  # noqa: F401,F403
from tests.arena_services.registration_rounds import *  # noqa: F401,F403
from tests.arena_services.reward_exchange import *  # noqa: F401,F403
from tests.arena_services.snapshot_matches import *  # noqa: F401,F403
