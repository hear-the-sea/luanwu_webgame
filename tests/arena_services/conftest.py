from __future__ import annotations

import pytest
from django.test import override_settings


@pytest.fixture(autouse=True)
def _legacy_arena_power_formula_for_arena_service_tests():
    """Keep arena reserve/fill mechanics tests on the v1 power scale.

    These tests assert reserve lifecycle, claims and billing behavior with
    hand-written target powers.  The v2 formula has its own focused coverage
    (test_arena_power_formula_v2.py and the virtual-player suites); pinning
    this directory avoids rewriting dozens of unrelated fixtures whenever the
    power scale changes.
    """

    with override_settings(ARENA_POWER_FORMULA_VERSION=1):
        yield
