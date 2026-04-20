"""
Compatibility entrypoint for raid combat battle tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/raid_combat_battle/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.raid_combat_battle.battle_flow import *  # noqa: F401,F403
from tests.raid_combat_battle.execution_cleanup import *  # noqa: F401,F403
from tests.raid_combat_battle.messaging import *  # noqa: F401,F403
from tests.raid_combat_battle.normalization_helpers import *  # noqa: F401,F403
from tests.raid_combat_battle.travel_blocking import *  # noqa: F401,F403
