from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

import guests.views.recruit as recruit_views
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import RecruitmentCandidate, RecruitmentPool
from guests.services.recruitment import recruit_guest


@pytest.mark.django_db
def test_recruit_view_rejects_when_action_lock_conflicts(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_recruit_lock_conflict", password="pass123")
    ensure_manor(user)
    client = Client()
    assert client.login(username="view_recruit_lock_conflict", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    called = {"count": 0}

    monkeypatch.setattr("guests.views.recruit._acquire_recruit_action_lock", lambda *_a, **_k: (False, "", None))

    def _unexpected_recruit(*_args, **_kwargs):
        called["count"] += 1
        return None

    monkeypatch.setattr("guests.views.recruit.start_guest_recruitment", _unexpected_recruit)

    response = client.post(reverse("guests:recruit"), {"pool": str(pool.pk)})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("请求处理中，请稍候重试" in m for m in messages)
    assert called["count"] == 0


@pytest.mark.django_db
def test_recruit_view_ajax_success_returns_recruitment_hall_partials(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="view_recruit_ajax_success", password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])
    client = Client()
    assert client.login(username="view_recruit_ajax_success", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    response = client.post(
        reverse("guests:recruit"),
        {"pool": str(pool.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "recruit-pools-section" in payload["hall_pools_html"]
    assert "recruit-candidates-section" in payload["hall_candidates_html"]
    assert "recruit-records-section" in payload["hall_records_html"]


@pytest.mark.django_db
def test_recruit_view_ajax_success_bypasses_cache_when_invalidation_fails(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_recruit_ajax_uncached", password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])
    client = Client()
    assert client.login(username="view_recruit_ajax_uncached", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")

    monkeypatch.setattr(
        "gameplay.selectors.recruitment._safe_cache_get",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache.get should be bypassed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.recruitment._safe_cache_set",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache.set should be bypassed")),
    )

    response = client.post(
        reverse("guests:recruit"),
        {"pool": str(pool.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "recruit-candidates-section" in payload["hall_candidates_html"]


@pytest.mark.django_db
def test_candidate_accept_view_rejects_when_action_lock_conflicts(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_lock_conflict", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_lock_conflict", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=1)[0]
    called = {"count": 0}

    monkeypatch.setattr("guests.views.recruit._acquire_recruit_action_lock", lambda *_a, **_k: (False, "", None))

    def _unexpected_finalize(*_args, **_kwargs):
        called["count"] += 1
        return [], []

    monkeypatch.setattr("guests.views.recruit._finalize_candidates", _unexpected_finalize)

    response = client.post(reverse("guests:candidate_accept"), {"candidate_ids": [str(candidate.pk)]})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("请求处理中，请稍候重试" in m for m in messages)
    assert called["count"] == 0


@pytest.mark.django_db
def test_candidate_accept_view_uses_manor_wide_candidate_action_lock(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_lock_scope", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_lock_scope", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=11)[0]
    captured: dict[str, str | int | None] = {}

    def _capture_lock(action, manor_id, scope):
        captured["action"] = action
        captured["manor_id"] = manor_id
        captured["scope"] = scope
        return False, "", None

    monkeypatch.setattr("guests.views.recruit._acquire_recruit_action_lock", _capture_lock)

    response = client.post(
        reverse("guests:candidate_accept"), {"candidate_ids": [str(candidate.pk)], "action": "retain"}
    )

    assert response.status_code == 302
    assert captured == {
        "action": "candidate_action",
        "manor_id": manor.id,
        "scope": f"candidate-actions:{manor.id}",
    }


@pytest.mark.django_db
def test_candidate_accept_view_retain_ajax_success_returns_recruitment_hall_partials(
    game_data, django_user_model, monkeypatch
):
    user = django_user_model.objects.create_user(username="view_candidate_accept_retain_ajax", password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])
    client = Client()
    assert client.login(username="view_candidate_accept_retain_ajax", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=6)[0]
    retainer_before = manor.retainer_count
    monkeypatch.setattr(
        "gameplay.selectors.recruitment._safe_cache_get",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache.get should be bypassed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.recruitment._safe_cache_set",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache.set should be bypassed")),
    )

    response = client.post(
        reverse("guests:candidate_accept"),
        {"candidate_ids": [str(candidate.pk)], "action": "retain"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "recruit-candidates-section" in payload["hall_candidates_html"]
    assert "已将 1 名候选收为家丁" in payload["message"]

    manor.refresh_from_db()
    assert manor.retainer_count == retainer_before + 1
    assert RecruitmentCandidate.objects.filter(pk=candidate.pk).exists() is False


@pytest.mark.django_db
def test_candidate_accept_view_rejects_invalid_candidate_ids(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="view_candidate_accept_invalid_ids", password="pass123")
    ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_invalid_ids", password="pass123")

    response = client.post(reverse("guests:candidate_accept"), {"candidate_ids": ["abc"], "action": "recruit"})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("候选门客选择有误" in m for m in messages)


@pytest.mark.django_db
def test_candidate_accept_view_discard_all_without_candidate_ids(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="view_candidate_accept_discard_all", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_discard_all", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    recruit_guest(manor, pool, seed=3)
    total = manor.candidates.count()
    assert total > 0

    response = client.post(reverse("guests:candidate_accept"), {"scope": "all", "action": "discard"})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert manor.candidates.count() == 0
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any(f"已放弃 {total} 名候选门客" in m for m in messages)


@pytest.mark.django_db
def test_candidate_accept_view_discard_all_uses_service_command(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_discard_service", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_discard_service", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidates = recruit_guest(manor, pool, seed=31)
    called: dict[str, object] = {}

    def _fake_discard(current_candidates):
        called["candidates"] = list(current_candidates)
        return len(current_candidates)

    monkeypatch.setattr("guests.views.recruit.discard_candidates", _fake_discard)

    response = client.post(reverse("guests:candidate_accept"), {"scope": "all", "action": "discard"})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert called["candidates"] == candidates


@pytest.mark.django_db
def test_candidate_accept_view_retain_uses_service_command(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_retain_service", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_retain_service", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=32)[0]
    called: dict[str, object] = {}

    def _fake_retain(current_candidates):
        called["candidates"] = list(current_candidates)
        return 1, None

    monkeypatch.setattr("guests.views.recruit.retain_candidates", _fake_retain)

    response = client.post(
        reverse("guests:candidate_accept"),
        {"candidate_ids": [str(candidate.pk)], "action": "retain"},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert called["candidates"] == [candidate]


@pytest.mark.django_db
def test_candidate_accept_view_accept_all_without_candidate_ids(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_accept_all", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_accept_all", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    recruit_guest(manor, pool, seed=4)

    called = {"count": 0}

    def _fake_finalize(candidates):
        called["count"] = len(candidates)
        return [], []

    monkeypatch.setattr("guests.views.recruit._finalize_candidates", _fake_finalize)

    response = client.post(reverse("guests:candidate_accept"), {"scope": "all", "action": "accept"})

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert called["count"] == manor.candidates.count()
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert not any("请先勾选候选门客" in m for m in messages)


@pytest.mark.django_db
def test_candidate_accept_view_rejects_invalid_action(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_invalid_action", password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username="view_candidate_accept_invalid_action", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=1)[0]
    called = {"finalize": 0, "retain": 0}

    def _unexpected_finalize(*_args, **_kwargs):
        called["finalize"] += 1
        return [], []

    def _unexpected_retain(*_args, **_kwargs):
        called["retain"] += 1
        return 0, None

    monkeypatch.setattr("guests.views.recruit._finalize_candidates", _unexpected_finalize)
    monkeypatch.setattr("guests.views.recruit.retain_candidates", _unexpected_retain)

    response = client.post(
        reverse("guests:candidate_accept"),
        {"candidate_ids": [str(candidate.pk)], "action": "invalid_action"},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("操作类型无效" in m for m in messages)
    assert called["finalize"] == 0
    assert called["retain"] == 0
    assert RecruitmentCandidate.objects.filter(pk=candidate.pk).exists()


@pytest.mark.django_db
def test_candidate_accept_view_truncates_success_message_for_large_batch(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_candidate_accept_large_batch", password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])
    client = Client()
    assert client.login(username="view_candidate_accept_large_batch", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = recruit_guest(manor, pool, seed=2)[0]

    class _DummyGuest:
        def __init__(self, display_name: str):
            self.display_name = display_name

    total = recruit_views.RECRUIT_SUCCESS_NAME_PREVIEW_LIMIT + 3
    succeeded = [_DummyGuest(f"门客{i}") for i in range(total)]

    monkeypatch.setattr(
        "guests.views.recruit._acquire_recruit_action_lock", lambda *_a, **_k: (True, "lock-test", "mock-token")
    )
    monkeypatch.setattr("guests.views.recruit._release_recruit_action_lock", lambda *_a, **_k: None)
    monkeypatch.setattr("guests.views.recruit._finalize_candidates", lambda *_a, **_k: (succeeded, []))

    response = client.post(
        reverse("guests:candidate_accept"), {"candidate_ids": [str(candidate.pk)], "action": "accept"}
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    message_list = [str(m) for m in get_messages(response.wsgi_request)]
    success_message = next((m for m in message_list if f"成功招募 {total} 名门客" in m), "")
    assert success_message
    assert "等 3 名" in success_message
    assert f"门客{recruit_views.RECRUIT_SUCCESS_NAME_PREVIEW_LIMIT}" not in success_message


@pytest.mark.django_db
def test_use_magnifying_glass_view_rejects_when_action_lock_conflicts_ajax(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="view_magnify_lock_conflict", password="pass123")
    ensure_manor(user)
    client = Client()
    assert client.login(username="view_magnify_lock_conflict", password="pass123")
    called = {"count": 0}

    monkeypatch.setattr("guests.views.recruit._acquire_recruit_action_lock", lambda *_a, **_k: (False, "", None))

    def _unexpected_reveal(*_args, **_kwargs):
        called["count"] += 1
        return 0

    monkeypatch.setattr("guests.views.recruit.use_magnifying_glass_for_candidates", _unexpected_reveal)

    response = client.post(
        reverse("guests:use_magnifying_glass"),
        {"item_id": "1"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["success"] is False
    assert "请求处理中，请稍候重试" in payload["error"]
    assert called["count"] == 0


@pytest.mark.django_db
def test_use_magnifying_glass_view_ajax_success_returns_recruitment_hall_partials(
    game_data, django_user_model, monkeypatch
):
    user = django_user_model.objects.create_user(username="view_magnify_ajax_success", password="pass123")
    manor = ensure_manor(user)
    manor.grain = manor.silver = 500000
    manor.save(update_fields=["grain", "silver"])
    client = Client()
    assert client.login(username="view_magnify_ajax_success", password="pass123")
    pool = RecruitmentPool.objects.get(key="cunmu")
    recruit_guest(manor, pool, seed=7)

    template, _created = ItemTemplate.objects.get_or_create(
        key="fangdajing",
        defaults={
            "name": "放大镜",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": True,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    monkeypatch.setattr(
        "gameplay.selectors.recruitment._safe_cache_get",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache.get should be bypassed")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.recruitment._safe_cache_set",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cache.set should be bypassed")),
    )

    response = client.post(
        reverse("guests:use_magnifying_glass"),
        {"item_id": str(item.pk)},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "显现" in payload["message"]
    assert "recruit-candidates-section" in payload["hall_candidates_html"]
