import logging


def test_timeconstants_are_shared():
    from common.constants.time import TimeConstants as CommonTime
    from gameplay.constants import TimeConstants as GameplayTime
    from guests.constants import TimeConstants as GuestsTime

    assert GameplayTime is CommonTime
    assert GuestsTime is CommonTime
    assert CommonTime.MINUTE == 60
    assert CommonTime.HOUR == 3600
    assert CommonTime.DAY == 86400


def test_resourcetype_is_shared():
    from common.constants.resources import ResourceType as CommonResourceType
    from gameplay.models import ResourceType as GameplayResourceType

    assert GameplayResourceType is CommonResourceType
    assert CommonResourceType.GRAIN == "grain"
    assert CommonResourceType.SILVER == "silver"


def test_resolve_drop_rewards_is_shared():
    from common.utils.loot import resolve_drop_rewards as common_resolve
    from gameplay.utils.loot_generator import resolve_drop_rewards as gameplay_resolve

    assert gameplay_resolve is common_resolve


def test_resolve_drop_rewards_rules_are_stable():
    from common.utils.loot import resolve_drop_rewards

    class DummyRng:
        def __init__(self, value: float):
            self._value = value

        def random(self) -> float:
            return self._value

    class SequenceRng:
        def __init__(self, values: list[float]):
            self._values = iter(values)

        def random(self) -> float:
            return next(self._values)

    rng_hit = DummyRng(0.0)
    rng_miss = DummyRng(0.999)

    assert resolve_drop_rewards({"silver": 100}, rng_hit) == {"silver": 100}
    assert resolve_drop_rewards({"rare": 0.5}, rng_hit) == {"rare": 1}
    assert resolve_drop_rewards({"rare": 0.5}, rng_miss) == {}

    # dict payload form: chance+count, and missing count defaults to 1.
    assert resolve_drop_rewards({"x": {"chance": 1, "count": 3}}, rng_miss) == {"x": 3}
    assert resolve_drop_rewards({"x": {"chance": 1}}, rng_miss) == {"x": 1}
    assert resolve_drop_rewards(
        {"pool": {"chance": 1, "choices": ["a", "b", "c"]}},
        SequenceRng([0.8]),
    ) == {"c": 1}
    assert resolve_drop_rewards(
        {
            "pool": {
                "chance": 0.5,
                "count": 2,
                "choices": [
                    {"key": "x", "weight": 1},
                    {"key": "y", "weight": 3},
                ],
            }
        },
        SequenceRng([0.0, 0.6]),
    ) == {"y": 2}


def test_resolve_drop_rewards_supports_random_count_ranges():
    from common.utils.loot import resolve_drop_rewards

    class RangeRng:
        def random(self) -> float:
            return 0.0

        def randint(self, minimum: int, maximum: int) -> int:
            assert (minimum, maximum) == (1, 5)
            return maximum

    assert resolve_drop_rewards(
        {"red_ruby": {"chance": 0.28, "count": {"min": 1, "max": 5}}},
        RangeRng(),
    ) == {"red_ruby": 5}


def test_safe_apply_async_swallows_dispatch_errors():
    from kombu.exceptions import OperationalError

    from common.utils.celery import safe_apply_async

    class DummyTask:
        def apply_async(self, *args, **kwargs):
            raise OperationalError("boom")

    logger = logging.getLogger("tests.safe_apply_async")
    assert safe_apply_async(DummyTask(), logger=logger) is False
