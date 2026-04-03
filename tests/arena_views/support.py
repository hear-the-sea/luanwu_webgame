from __future__ import annotations

import pytest
from django.test import Client

from gameplay.services.manor.core import ensure_manor


@pytest.fixture
def arena_client(django_user_model):
    user = django_user_model.objects.create_user(
        username="arena_view_user",
        password="testpass123",
        email="arena_view_user@test.local",
    )
    client = Client()
    client.login(username="arena_view_user", password="testpass123")
    manor = ensure_manor(user)
    manor.silver = 100000
    manor.save(update_fields=["silver"])
    return client, manor
