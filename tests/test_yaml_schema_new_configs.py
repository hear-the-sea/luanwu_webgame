"""
Compatibility entrypoint for additional YAML schema tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/yaml_schema_new_configs/` submodules while this path remains stable for
existing pytest commands and CI references.
"""

from tests.yaml_schema_new_configs.guest_arena_rules import *  # noqa: F401,F403
from tests.yaml_schema_new_configs.guild_growth_technology import *  # noqa: F401,F403
from tests.yaml_schema_new_configs.production_rules import *  # noqa: F401,F403
from tests.yaml_schema_new_configs.virtual_players import *  # noqa: F401,F403
from tests.yaml_schema_new_configs.warehouse_auction_forge import *  # noqa: F401,F403
