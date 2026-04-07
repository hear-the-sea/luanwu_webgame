from __future__ import annotations

from django.core.management import call_command
from django.test import Client

from gameplay.services.manor.core import ensure_manor
from guests.models import RecruitmentPool
from guests.services.recruitment import recruit_guest
from guests.services.recruitment_guests import finalize_candidate


def bootstrap_guest_client(game_data, django_user_model, *, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])

    pool = RecruitmentPool.objects.filter(key="cunmu").first()
    if pool is None:
        call_command("load_guest_templates", verbosity=0, skip_images=True)
        pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=1)[0]
    guest = finalize_candidate(candidate)

    client = Client()
    assert client.login(username=username, password="pass123")
    return manor, guest, client
