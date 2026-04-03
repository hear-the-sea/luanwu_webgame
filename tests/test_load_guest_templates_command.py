"""
Compatibility entrypoint for load_guest_templates command tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/load_guest_templates_command/` submodules while this path remains stable
for existing pytest commands and CI references.
"""

from tests.load_guest_templates_command.command_helper_cases import *  # noqa: F401,F403
from tests.load_guest_templates_command.import_sync_cases import *  # noqa: F401,F403
from tests.load_guest_templates_command.special_payload_cases import *  # noqa: F401,F403
