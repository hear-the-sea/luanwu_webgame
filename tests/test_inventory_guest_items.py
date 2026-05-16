"""
Compatibility entrypoint for inventory guest item tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/inventory_guest_items/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.inventory_guest_items.rarity_upgrade import *  # noqa: F401,F403
from tests.inventory_guest_items.reset_items import *  # noqa: F401,F403
from tests.inventory_guest_items.soul_container import *  # noqa: F401,F403
from tests.inventory_guest_items.summon_guest_items import *  # noqa: F401,F403
