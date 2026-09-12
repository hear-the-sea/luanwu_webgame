"""
Compatibility entrypoint for guest summon card tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/guest_summon_card/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.guest_summon_card.avatars import *  # noqa: F401,F403
from tests.guest_summon_card.loot_box_config import *  # noqa: F401,F403
from tests.guest_summon_card.loot_boxes import *  # noqa: F401,F403
from tests.guest_summon_card.summon_flows import *  # noqa: F401,F403
from tests.guest_summon_card.summon_validation import *  # noqa: F401,F403
from tests.guest_summon_card.utility_items import *  # noqa: F401,F403
