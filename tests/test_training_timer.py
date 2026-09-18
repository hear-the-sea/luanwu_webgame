import pytest
from django.utils import timezone

from core.exceptions import GuestTrainingUnavailableError
from gameplay.services.manor.core import ensure_manor
from guests.models import RecruitmentPool
from guests.services.recruitment import recruit_guest
from guests.services.recruitment_guests import finalize_candidate
from guests.services.training import ensure_auto_training, reduce_training_time, reduce_training_time_for_guest
from guests.training_runtime import ensure_training_timer as ensure_training_timer_runtime
from guests.utils.training_timer import ensure_training_timer, remaining_training_seconds


def _bootstrap_guest(django_user_model):
    user = django_user_model.objects.create_user(username="player_timer", password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 5000
    manor.save()
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=1)[0]
    guest = finalize_candidate(candidate)
    return guest


def test_ensure_training_timer_refreshes_after_finalization():
    class GuestStub:
        level = 1
        training_complete_at = "expired"
        refreshed = False

        def refresh_from_db(self):
            self.refreshed = True
            self.level = 2
            self.training_complete_at = "next"

    guest = GuestStub()
    finalize_calls = []

    def finalize(_guest, *, now):
        finalize_calls.append(now)
        return True

    def unexpected_auto_training(_guest):
        raise AssertionError("the refreshed timer should already be present")

    assert (
        ensure_training_timer_runtime(
            guest,
            now="now",
            finalize_guest_training_func=finalize,
            ensure_auto_training_func=unexpected_auto_training,
        )
        is True
    )
    assert finalize_calls == ["now"]
    assert guest.refreshed is True
    assert guest.training_complete_at == "next"


@pytest.mark.django_db
def test_ensure_training_timer_creates_timer(game_data, django_user_model):
    guest = _bootstrap_guest(django_user_model)
    guest.training_complete_at = None
    guest.training_target_level = 0
    guest.save(update_fields=["training_complete_at", "training_target_level"])

    assert ensure_training_timer(guest) is True
    guest.refresh_from_db()
    assert guest.training_complete_at is not None
    assert remaining_training_seconds(guest) > 0


@pytest.mark.django_db
def test_reduce_training_time_for_guest_levels_up(game_data, django_user_model):
    guest = _bootstrap_guest(django_user_model)
    ensure_auto_training(guest)
    guest.refresh_from_db()
    guest.training_complete_at = timezone.now() + timezone.timedelta(seconds=5)
    guest.training_target_level = guest.level + 1
    guest.save(update_fields=["training_complete_at", "training_target_level"])

    result = reduce_training_time_for_guest(guest, seconds=10)
    guest.refresh_from_db()

    assert guest.level >= 2
    assert result["applied_levels"] >= 1
    assert result["time_reduced"] > 0


@pytest.mark.django_db
def test_reduce_training_time_rejects_when_no_eligible_guests(game_data, django_user_model):
    guest = _bootstrap_guest(django_user_model)
    guest.level = 100
    guest.status = "idle"
    guest.training_complete_at = None
    guest.training_target_level = 0
    guest.save(update_fields=["level", "status", "training_complete_at", "training_target_level"])

    with pytest.raises(GuestTrainingUnavailableError, match="所有门客已达等级上限"):
        reduce_training_time(guest.manor, seconds=10)


@pytest.mark.django_db
def test_reduce_training_time_rejects_non_positive_seconds(game_data, django_user_model):
    guest = _bootstrap_guest(django_user_model)

    with pytest.raises(AssertionError, match="invalid guest training seconds"):
        reduce_training_time(guest.manor, seconds=0)


@pytest.mark.django_db
def test_reduce_training_time_for_guest_rejects_non_positive_seconds(game_data, django_user_model):
    guest = _bootstrap_guest(django_user_model)

    with pytest.raises(AssertionError, match="invalid guest training seconds"):
        reduce_training_time_for_guest(guest, seconds=0)
