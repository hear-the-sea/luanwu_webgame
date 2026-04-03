"""
Compatibility entrypoint for arena view tests.

The original file exceeded the audit size threshold. Tests now live in
`tests/arena_views/` submodules while this path remains stable for existing
pytest commands and CI references.
"""

from tests.arena_views.detail_pages import *  # noqa: F401,F403
from tests.arena_views.error_boundaries import *  # noqa: F401,F403
from tests.arena_views.registration_pages import *  # noqa: F401,F403
