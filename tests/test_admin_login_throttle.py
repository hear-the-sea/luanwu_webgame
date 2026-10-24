from uuid import uuid4

import pytest
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.cache.backends.locmem import LocMemCache
from django.test import Client, RequestFactory
from django.urls import reverse

from accounts import views as account_views
from accounts.admin_authentication import ThrottledAdminAuthenticationForm

pytestmark = pytest.mark.django_db
PASSWORD = "AdminLoginTest123!"
IP = "203.0.113.120"


@pytest.fixture(autouse=True)
def isolated_throttle(monkeypatch):
    isolated = LocMemCache(f"admin-authentication-{uuid4()}", {})
    monkeypatch.setattr(account_views, "cache", isolated)
    monkeypatch.setattr(account_views, "_LOCAL_LOGIN_CACHE", {})
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_LIMIT", 3)
    monkeypatch.setattr(account_views, "LOGIN_IP_ATTEMPT_LIMIT", 5)
    return isolated


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user(username="throttled_staff", password=PASSWORD, is_staff=True)


def _keys(username):
    request = RequestFactory().post(reverse("admin:login"), REMOTE_ADDR=IP)
    return account_views._get_login_attempt_key(request, username), account_views._get_login_lock_key(request, username)


def test_admin_failures_use_the_shared_account_and_ip_counters(client, staff, isolated_throttle):
    response = client.post(reverse("admin:login"), {"username": staff.username, "password": "wrong"}, REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert isinstance(response.context["form"], AdminAuthenticationForm)
    assert isinstance(response.context["form"], ThrottledAdminAuthenticationForm)
    assert all(isolated_throttle.get(key) == 1 for key in _keys(staff.username)[0])
    assert "_auth_user_id" not in client.session
    assert response.context["site_title"]
    assert response.context["app_path"] == reverse("admin:login")
    assert response.context["next"] == reverse("admin:index")
    assert "no-store" in response["Cache-Control"]


def test_admin_account_lock_cannot_be_bypassed_via_normal_login(client, staff):
    for _ in range(account_views.LOGIN_ATTEMPT_LIMIT):
        client.post(reverse("admin:login"), {"username": staff.username, "password": "wrong"}, REMOTE_ADDR=IP)
    response = client.post(
        reverse("accounts:login"), {"username": staff.username, "password": PASSWORD}, REMOTE_ADDR="198.51.100.121"
    )
    assert response.status_code == 200
    assert "登录尝试次数过多" in response.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("lock_scope", ["account", "ip"])
def test_normal_login_lock_blocks_admin_before_password_authentication(client, staff, monkeypatch, lock_scope):
    if lock_scope == "account":
        for _ in range(account_views.LOGIN_ATTEMPT_LIMIT):
            client.post(reverse("accounts:login"), {"username": staff.username, "password": "wrong"}, REMOTE_ADDR=IP)
        remote_ip = "198.51.100.121"
    else:
        for index in range(account_views.LOGIN_IP_ATTEMPT_LIMIT):
            client.post(
                reverse("accounts:login"), {"username": f"unknown_staff_{index}", "password": "wrong"}, REMOTE_ADDR=IP
            )
        remote_ip = IP

    def forbidden(*_args, **_kwargs):
        raise AssertionError("locked admin request attempted password authentication")

    monkeypatch.setattr("django.contrib.auth.forms.authenticate", forbidden)
    response = client.post(
        reverse("admin:login"), {"username": staff.username, "password": PASSWORD}, REMOTE_ADDR=remote_ip
    )
    assert response.status_code == 200
    assert "登录尝试次数过多" in response.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("active,staff_status", [(True, False), (False, True)])
def test_correct_password_still_requires_active_staff(
    client, django_user_model, isolated_throttle, active, staff_status
):
    user = django_user_model.objects.create_user(
        username="not_admin", password=PASSWORD, is_active=active, is_staff=staff_status
    )
    response = client.post(reverse("admin:login"), {"username": user.username, "password": PASSWORD}, REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert "_auth_user_id" not in client.session
    assert all(isolated_throttle.get(key) == 1 for key in _keys(user.username)[0])


def test_staff_success_clears_only_account_bucket_and_keeps_ip_evidence(client, staff, isolated_throttle):
    request = RequestFactory().post(reverse("accounts:login"), REMOTE_ADDR=IP)
    account_views._record_failed_attempt(request, staff.username)
    attempts, locks = _keys(staff.username)
    response = client.post(
        reverse("admin:login"),
        {"username": staff.username, "password": PASSWORD, "next": reverse("admin:index")},
        REMOTE_ADDR=IP,
    )
    assert response.status_code == 302
    assert response.url == reverse("admin:index")
    assert client.session["_auth_user_id"] == str(staff.pk)
    assert isolated_throttle.get(attempts[0]) == 1
    assert isolated_throttle.get(attempts[1]) is None
    assert isolated_throttle.get(locks[1]) is None
    assert account_views._local_login_cache_get(attempts[0]) == 1
    assert account_views._local_login_cache_get(attempts[1]) is None


def test_admin_login_without_next_uses_django_default_redirect(client, staff):
    response = client.post(reverse("admin:login"), {"username": staff.username, "password": PASSWORD}, REMOTE_ADDR=IP)
    assert response.status_code == 302
    assert response.url == "/"


@pytest.mark.parametrize("next_url", ["https://attacker.example/admin/", "//attacker.example/admin/"])
def test_admin_login_rejects_external_next_redirect(client, staff, next_url):
    response = client.post(
        reverse("admin:login"), {"username": staff.username, "password": PASSWORD, "next": next_url}, REMOTE_ADDR=IP
    )
    assert response.status_code == 302
    assert response.url == "/"
    assert client.session["_auth_user_id"] == str(staff.pk)


def test_admin_login_keeps_allowed_local_next_redirect(client, staff):
    destination = reverse("admin:accounts_user_changelist")
    response = client.post(
        reverse("admin:login"), {"username": staff.username, "password": PASSWORD, "next": destination}, REMOTE_ADDR=IP
    )
    assert response.status_code == 302
    assert response.url == destination


def test_admin_login_still_enforces_csrf(staff):
    client = Client(enforce_csrf_checks=True)
    response = client.post(reverse("admin:login"), {"username": staff.username, "password": PASSWORD}, REMOTE_ADDR=IP)
    assert response.status_code == 403
    assert "_auth_user_id" not in client.session
    assert all(account_views.cache.get(key) is None for key in _keys(staff.username)[0])


def test_admin_login_remains_public_with_django_login_required_middleware(client, settings):
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "django.contrib.auth.middleware.LoginRequiredMiddleware"]
    response = client.get(reverse("admin:login"), REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert isinstance(response.context["form"], ThrottledAdminAuthenticationForm)


def test_malformed_admin_login_counts_ip_failure(client, isolated_throttle):
    response = client.post(reverse("admin:login"), {"username": "", "password": ""}, REMOTE_ADDR=IP)
    assert response.status_code == 200
    assert isolated_throttle.get(_keys("")[0][0]) == 1
