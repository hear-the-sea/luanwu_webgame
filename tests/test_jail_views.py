"""
Compatibility entrypoint for jail and oath grove view tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/jail_views/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.jail_views.api_actions import *  # noqa: F401,F403
from tests.jail_views.form_views import *  # noqa: F401,F403
from tests.jail_views.page_context import *  # noqa: F401,F403
from tests.jail_views.persuasion_api import *  # noqa: F401,F403
