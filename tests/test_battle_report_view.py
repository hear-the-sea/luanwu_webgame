"""
Compatibility entrypoint for battle report view tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/battle_report_view/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.battle_report_view.access_and_perspectives import *  # noqa: F401,F403
from tests.battle_report_view.passive_rendering import *  # noqa: F401,F403
from tests.battle_report_view.raid_cases import *  # noqa: F401,F403
