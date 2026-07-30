from __future__ import annotations

import threading
import uuid

import pytest
from django.db import IntegrityError, close_old_connections, connection

from gameplay.models import BotProfile, Manor
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

pytestmark = [pytest.mark.integration]


def _configure_minimal_virtual_player_projection(settings) -> None:
    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        }
    }


def _occupy_location(django_user_model, *, coordinate: tuple[int, int]) -> Manor:
    suffix = uuid.uuid4().hex[:8]
    owner = django_user_model.objects.create_user(username=f"coordinate_owner_{suffix}", password="pass123")
    manor = ensure_manor(owner, region="north")
    manor.region = "north"
    manor.coordinate_x, manor.coordinate_y = coordinate
    manor.save(update_fields=["region", "coordinate_x", "coordinate_y"])
    return manor


def _minimal_projection() -> BotProjectionConfig:
    return BotProjectionConfig(prestige=100, building_level=1, guest_count=0, guest_level=1)


@pytest.mark.django_db(transaction=True)
def test_virtual_player_retries_real_coordinate_conflict_inside_savepoint(
    settings,
    django_user_model,
    monkeypatch,
):
    if connection.vendor != "mysql":
        pytest.skip("virtual player coordinate retry requires MySQL unique-index semantics")

    from gameplay.services.virtual_player_core import bootstrap as virtual_players

    _configure_minimal_virtual_player_projection(settings)
    occupied_coordinate = (981, 982)
    available_coordinate = (983, 984)
    _occupy_location(django_user_model, coordinate=occupied_coordinate)
    initial_user_count = django_user_model.objects.count()
    initial_manor_count = Manor.objects.count()
    initial_profile_count = BotProfile.objects.count()
    candidates = iter([occupied_coordinate, available_coordinate])
    coordinate_calls = 0
    projection_calls = 0
    original_project_buildings = virtual_players._project_buildings

    def _next_coordinate(_region: str) -> tuple[int, int]:
        nonlocal coordinate_calls
        coordinate_calls += 1
        return next(candidates)

    def _count_project_buildings(*args, **kwargs) -> None:
        nonlocal projection_calls
        projection_calls += 1
        original_project_buildings(*args, **kwargs)

    monkeypatch.setattr(virtual_players, "generate_unique_coordinate", _next_coordinate)
    monkeypatch.setattr(virtual_players, "_project_buildings", _count_project_buildings)

    profile = create_virtual_player(
        region="north",
        prestige_band="newbie",
        growth_seed=91001,
        projection=_minimal_projection(),
    )

    profile.manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y"])
    assert (profile.manor.coordinate_x, profile.manor.coordinate_y) == available_coordinate
    assert coordinate_calls == 2
    assert projection_calls == 1
    assert django_user_model.objects.count() == initial_user_count + 1
    assert Manor.objects.count() == initial_manor_count + 1
    assert BotProfile.objects.count() == initial_profile_count + 1
    assert BotProfile.objects.filter(pk=profile.pk, manor=profile.manor).count() == 1


@pytest.mark.django_db(transaction=True)
def test_virtual_player_real_coordinate_conflicts_exhaust_and_roll_back_outer_transaction(
    settings,
    django_user_model,
    monkeypatch,
):
    if connection.vendor != "mysql":
        pytest.skip("virtual player coordinate retry requires MySQL unique-index semantics")

    from gameplay.services.virtual_player_core import bootstrap as virtual_players

    _configure_minimal_virtual_player_projection(settings)
    occupied_coordinate = (985, 986)
    _occupy_location(django_user_model, coordinate=occupied_coordinate)
    initial_user_count = django_user_model.objects.count()
    initial_manor_count = Manor.objects.count()
    initial_profile_count = BotProfile.objects.count()
    coordinate_calls = 0
    projection_calls = 0
    original_project_buildings = virtual_players._project_buildings

    def _occupied_coordinate(_region: str) -> tuple[int, int]:
        nonlocal coordinate_calls
        coordinate_calls += 1
        return occupied_coordinate

    def _count_project_buildings(*args, **kwargs) -> None:
        nonlocal projection_calls
        projection_calls += 1
        original_project_buildings(*args, **kwargs)

    monkeypatch.setattr(virtual_players, "generate_unique_coordinate", _occupied_coordinate)
    monkeypatch.setattr(virtual_players, "_project_buildings", _count_project_buildings)

    with pytest.raises(IntegrityError):
        create_virtual_player(
            region="north",
            prestige_band="newbie",
            growth_seed=91002,
            projection=_minimal_projection(),
        )

    assert coordinate_calls == 5
    assert projection_calls == 1
    assert django_user_model.objects.count() == initial_user_count
    assert Manor.objects.count() == initial_manor_count
    assert BotProfile.objects.count() == initial_profile_count


@pytest.mark.django_db(transaction=True)
def test_concurrent_manor_assignment_retries_shared_first_coordinate(django_user_model, monkeypatch):
    if connection.vendor != "mysql":
        pytest.skip("manor coordinate concurrency requires MySQL unique-index semantics")

    suffix = uuid.uuid4().hex[:8]
    users = [
        django_user_model.objects.create_user(username=f"manor_coordinate_{suffix}_{index}", password="pass123")
        for index in range(2)
    ]
    Manor.objects.filter(user_id__in=[user.id for user in users]).update(
        region="north",
        coordinate_x=0,
        coordinate_y=0,
    )

    first_candidate_barrier = threading.Barrier(2)
    thread_state = threading.local()
    generator_calls: dict[int, int] = {}
    generator_calls_guard = threading.Lock()
    results: list[tuple[int, int, int]] = []
    errors: list[BaseException] = []

    def _contended_coordinate(_region: str) -> tuple[int, int]:
        call_index = int(getattr(thread_state, "coordinate_calls", 0))
        thread_state.coordinate_calls = call_index + 1
        with generator_calls_guard:
            generator_calls[threading.get_ident()] = call_index + 1
        if call_index == 0:
            first_candidate_barrier.wait(timeout=10)
            return 991, 992
        return 993, 994

    monkeypatch.setattr("gameplay.services.manor.core.generate_unique_coordinate", _contended_coordinate)

    def _worker(user_id: int) -> None:
        close_old_connections()
        try:
            user = django_user_model.objects.get(pk=user_id)
            manor = ensure_manor(user, region="north")
            results.append((manor.id, manor.coordinate_x, manor.coordinate_y))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(user.id,)) for user in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert {(x, y) for _manor_id, x, y in results} == {(991, 992), (993, 994)}
    assert sorted(generator_calls.values()) == [1, 2]
    assert (
        Manor.objects.filter(
            occupied_region="north",
            coordinate_x__gt=0,
            coordinate_y__gt=0,
            user_id__in=[user.id for user in users],
        ).count()
        == 2
    )
