from uuid import uuid4

import pytest
from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.cache.backends.locmem import LocMemCache
from django.test import Client, RequestFactory
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError

from accounts import admin_login
from accounts import views as account_views

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_login_cache(monkeypatch):
    # 独立缓存只存放本文件的测试数据，不清空应用缓存。
    isolated_cache = LocMemCache(f"admin-login-restrictions-{uuid4()}", {})
    monkeypatch.setattr(account_views, "cache", isolated_cache)
    monkeypatch.setattr(admin_login, "cache", isolated_cache)
    monkeypatch.setattr(account_views, "_LOCAL_LOGIN_CACHE", {})
    return isolated_cache


@pytest.fixture
def restriction_url():
    return reverse("admin:accounts_user_clear_login_restrictions")


@pytest.fixture
def operator(django_user_model):
    user = django_user_model.objects.create_user(username="restriction_operator", is_staff=True)
    user.user_permissions.add(Permission.objects.get(content_type__app_label="accounts", codename="change_user"))
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


def restriction_keys(username="blocked_user", ip="203.0.113.51"):
    request = RequestFactory().post("/login/", REMOTE_ADDR=ip)
    ip_attempt, user_attempt = account_views._get_login_attempt_key(request, username)
    ip_lock, user_lock = account_views._get_login_lock_key(request, username)
    return {"username": (user_attempt, user_lock), "ip": (ip_attempt, ip_lock)}


def seed_restrictions(keys):
    for key in keys:
        account_views.cache.set(key, 5, timeout=60)
        account_views._local_login_cache_set(key, 5, timeout=60, unsynced=True)


@pytest.mark.parametrize("targets", [("username",), ("ip",), ("username", "ip")])
def test_admin_clears_only_selected_restrictions(operator_client, restriction_url, targets, operator):
    buckets = restriction_keys()
    selected_keys = {key for target in targets for key in buckets[target]}
    all_keys = {key for keys in buckets.values() for key in keys}
    other_keys = {key for keys in restriction_keys("other_user", "198.51.100.52").values() for key in keys}
    other_keys.add("gameplay:unrelated-business-cache")
    seed_restrictions(all_keys | other_keys)
    inputs = {"username": "blocked_user", "ip": "203.0.113.51"}

    response = operator_client.post(restriction_url, {target: inputs[target] for target in targets})

    assert response.status_code == 302
    assert response.url == reverse("admin:accounts_user_changelist")
    for key in selected_keys:
        assert account_views.cache.get(key) is None
        assert account_views._local_login_cache_get(key) is None
    for key in (all_keys - selected_keys) | other_keys:
        assert account_views.cache.get(key) == 5
        assert account_views._local_login_cache_get(key) == 5
    assert any(message.level == messages.SUCCESS for message in get_messages(response.wsgi_request))
    entry = LogEntry.objects.get(user=operator)
    assert entry.action_flag == CHANGE
    audit_text = entry.object_repr + entry.change_message
    for target in targets:
        assert inputs[target] in audit_text


@pytest.mark.parametrize("scope", ["username", "ip", "both"])
def test_admin_unlocks_actual_login_check(operator_client, restriction_url, scope):
    request = RequestFactory().post("/login/", REMOTE_ADDR="203.0.113.51")
    targets = ("username", "ip") if scope == "both" else (scope,)
    buckets = restriction_keys()
    seed_restrictions(key for target in targets for key in buckets[target])
    assert account_views._check_login_attempts(request, "blocked_user")[0] is True

    response = operator_client.post(
        restriction_url,
        {"username": "blocked_user", "ip": "203.0.113.51"},
    )

    assert response.status_code == 302
    assert account_views._check_login_attempts(request, "blocked_user") == (False, 0)


def test_admin_normalizes_username_like_login(operator_client, restriction_url):
    keys = restriction_keys("blocked_user")["username"]
    seed_restrictions(keys)

    response = operator_client.post(restriction_url, {"username": "  ＢＬＯＣＫＥＤ_USER  "})

    assert response.status_code == 302
    assert all(account_views.cache.get(key) is None for key in keys)


def test_valid_password_login_succeeds_after_admin_unlock(operator_client, django_user_model, restriction_url):
    user = django_user_model.objects.create_user(username="blocked_user", password="StrongPass123!")
    login_client = Client()
    request = RequestFactory().post("/login/", REMOTE_ADDR="203.0.113.51")
    for _ in range(account_views.LOGIN_ATTEMPT_LIMIT):
        account_views._record_failed_attempt(request, user.username)
    credentials = {"username": user.username, "password": "StrongPass123!"}
    login_url = reverse("accounts:login")

    locked_response = login_client.post(login_url, credentials, REMOTE_ADDR="203.0.113.51")
    assert locked_response.status_code == 200
    assert "登录尝试次数过多" in locked_response.content.decode()
    assert "_auth_user_id" not in login_client.session

    response = operator_client.post(restriction_url, {"username": user.username})
    assert response.status_code == 302
    unlocked_response = login_client.post(login_url, credentials, REMOTE_ADDR="203.0.113.51")
    assert unlocked_response.status_code == 302
    assert login_client.session["_auth_user_id"] == str(user.pk)


def test_admin_preserves_ipv6_text_used_by_login(operator_client, restriction_url):
    ip_address = "2001:0db8:0000:0000:0000:0000:0000:0001"
    keys = restriction_keys(ip=ip_address)["ip"]
    seed_restrictions(keys)

    response = operator_client.post(restriction_url, {"ip": ip_address})

    assert response.status_code == 302
    assert all(account_views.cache.get(key) is None for key in keys)


def test_get_form_does_not_clear_restrictions(operator_client, restriction_url):
    keys = restriction_keys()["username"]
    seed_restrictions(keys)

    response = operator_client.get(restriction_url, {"username": "blocked_user"})

    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()
    assert 'name="ip"' in response.content.decode()
    assert all(account_views.cache.get(key) == 5 for key in keys)
    assert not LogEntry.objects.exists()


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"username": "   ", "ip": " "},
        {"ip": "not-an-ip"},
        {"username": "blocked_user", "ip": "203.0.113.999"},
    ],
)
def test_invalid_form_does_not_clear_restrictions(operator_client, restriction_url, inputs):
    keys = restriction_keys()["username"]
    seed_restrictions(keys)

    response = operator_client.post(restriction_url, inputs)

    assert response.status_code == 200
    assert response.context["form"].errors
    assert all(account_views.cache.get(key) == 5 for key in keys)
    assert not LogEntry.objects.exists()


@pytest.mark.parametrize("identity", ["anonymous", "regular", "staff", "view_only"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_clear_restrictions_requires_change_user_permission(
    client, django_user_model, restriction_url, identity, method
):
    keys = restriction_keys()["username"]
    seed_restrictions(keys)
    if identity != "anonymous":
        user = django_user_model.objects.create_user(
            username=f"restricted_{identity}",
            is_staff=identity in ("staff", "view_only"),
        )
        if identity == "view_only":
            user.user_permissions.add(Permission.objects.get(content_type__app_label="accounts", codename="view_user"))
        client.force_login(user)

    response = getattr(client, method)(restriction_url, {"username": "blocked_user"})

    assert response.status_code == (403 if identity in ("staff", "view_only") else 302)
    assert all(account_views.cache.get(key) == 5 for key in keys)
    assert not LogEntry.objects.exists()


def test_clear_restrictions_requires_csrf_token(operator, restriction_url):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    keys = restriction_keys()["username"]
    seed_restrictions(keys)

    response = client.post(restriction_url, {"username": "blocked_user"})

    assert response.status_code == 403
    assert all(account_views.cache.get(key) == 5 for key in keys)
    assert not LogEntry.objects.exists()


@pytest.mark.parametrize("failure", ["delete_error", "read_error", "retained_lock"])
def test_cache_failure_does_not_report_success(operator_client, restriction_url, monkeypatch, failure):
    keys = restriction_keys()["username"]
    seed_restrictions(keys)

    def unavailable(*args, **kwargs):
        raise RedisConnectionError("test cache unavailable")

    if failure == "delete_error":
        monkeypatch.setattr(account_views.cache, "delete_many", unavailable)
    elif failure == "read_error":
        monkeypatch.setattr(account_views.cache, "get_many", unavailable)
    else:
        monkeypatch.setattr(account_views.cache, "delete_many", lambda _keys: None)

    response = operator_client.post(restriction_url, {"username": "blocked_user"})

    assert response.status_code == 200
    assert response.context["form"].errors
    assert not any(message.level == messages.SUCCESS for message in get_messages(response.wsgi_request))
    assert not LogEntry.objects.exists()
    assert all(account_views._local_login_cache_get(key) == 5 for key in keys)


@pytest.mark.parametrize("can_change", [True, False])
def test_changelist_shows_restrictions_link_only_with_change_permission(
    client, django_user_model, restriction_url, can_change
):
    user = django_user_model.objects.create_user(username="list_operator", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="accounts",
            codename="change_user" if can_change else "view_user",
        )
    )
    client.force_login(user)

    response = client.get(reverse("admin:accounts_user_changelist"))

    assert response.status_code == 200
    assert (f'href="{restriction_url}"' in response.content.decode()) is can_change
    assert ("解除登录限制" in response.content.decode()) is can_change
