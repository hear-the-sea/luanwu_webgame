from __future__ import annotations

from gameplay.selectors import arena as arena_selectors
from gameplay.selectors.arena.details import get_arena_coop_event_detail_context, get_arena_event_detail_context
from gameplay.selectors.arena.events import get_arena_events_context
from gameplay.selectors.arena.registration import get_arena_exchange_context, get_arena_registration_context
from gameplay.services import arena as arena_services


def test_arena_service_package_is_marker_only():
    assert getattr(arena_services, "__all__", []) == []


def test_arena_selector_package_is_marker_only():
    assert getattr(arena_selectors, "__all__", []) == []


def test_explicit_arena_selector_submodules_expose_page_entrypoints():
    assert callable(get_arena_registration_context)
    assert callable(get_arena_events_context)
    assert callable(get_arena_exchange_context)
    assert callable(get_arena_event_detail_context)
    assert callable(get_arena_coop_event_detail_context)
