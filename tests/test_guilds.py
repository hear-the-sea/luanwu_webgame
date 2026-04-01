"""
Compatibility entrypoint for guild service tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guilds/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.guilds.lifecycle import *  # noqa: F401,F403
from tests.guilds.membership import *  # noqa: F401,F403
from tests.guilds.test_contribution_upgrade import *  # noqa: F401,F403
from tests.guilds.test_creation import *  # noqa: F401,F403
