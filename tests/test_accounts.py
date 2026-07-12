from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
from django.test import RequestFactory, override_settings
from django.urls import reverse
from redis.exceptions import RedisError

from accounts import views as account_views
from accounts.forms import SignUpForm
from gameplay.services.manor.core import ensure_manor

User = get_user_model()


@pytest.mark.django_db
def test_user_can_register(client):
    response = client.post(
        reverse("accounts:register"),
        {
            "username": "test-user",
            "email": "test@example.com",
            "manor_name": "先锋庄园",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        },
    )
    assert response.status_code == 302
    user = User.objects.get(username="test-user")
    assert user.manor.name == "先锋庄园"


@pytest.mark.django_db
def test_register_view_rate_limits_repeated_requests_from_same_ip(client):
    cache.clear()
    url = reverse("accounts:register")

    for index in range(account_views.REGISTRATION_IP_LIMIT):
        response = client.post(url, {"username": f"invalid-{index}"}, REMOTE_ADDR="203.0.113.10")
        assert response.status_code == 200

    limited = client.post(url, {"username": "still-invalid"}, REMOTE_ADDR="203.0.113.10")

    assert limited.status_code == 302
    assert limited["Location"] == url
    assert User.objects.filter(username="still-invalid").exists() is False


@pytest.mark.django_db
def test_register_view_rate_limits_normalized_email_across_ips(client):
    cache.clear()
    url = reverse("accounts:register")

    for index in range(account_views.REGISTRATION_EMAIL_LIMIT):
        response = client.post(
            url,
            {"username": f"invalid-email-{index}", "email": " Target@Example.com "},
            REMOTE_ADDR=f"203.0.113.{20 + index}",
        )
        assert response.status_code == 200

    limited = client.post(
        url,
        {"username": "email-limited", "email": "target@example.com"},
        REMOTE_ADDR="203.0.113.99",
    )

    assert limited.status_code == 302
    assert limited["Location"] == url


@pytest.mark.django_db
def test_register_view_fails_closed_when_rate_limit_cache_is_unavailable(client, monkeypatch):
    cache.clear()
    url = reverse("accounts:register")

    def _raise_cache_error(*_args, **_kwargs):
        raise RedisError("cache unavailable")

    monkeypatch.setattr(cache, "add", _raise_cache_error)
    response = client.post(
        url,
        {
            "username": "cache-failure-user",
            "email": "cache-failure@example.com",
            "manor_name": "缓存故障庄园",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        },
        REMOTE_ADDR="203.0.113.120",
    )

    assert response.status_code == 302
    assert response["Location"] == url
    assert User.objects.filter(username="cache-failure-user").exists() is False


@pytest.mark.django_db
def test_user_email_blank_is_normalized_to_null():
    user = User.objects.create_user(username="email_null_user", password="pass123")
    user.refresh_from_db()
    assert user.email is None


@pytest.mark.django_db
def test_user_email_unique_constraint_is_enforced():
    User.objects.create_user(username="email_unique_1", email="Dup@Example.com", password="pass123")
    with pytest.raises(IntegrityError):
        User.objects.create_user(username="email_unique_2", email="dup@example.com", password="pass123")


@pytest.mark.django_db
def test_signup_form_rejects_duplicate_email_case_insensitive():
    User.objects.create_user(username="email_form_exists", email="used@example.com", password="pass123")
    form = SignUpForm(
        data={
            "username": "email_form_new",
            "email": " USED@EXAMPLE.COM ",
            "manor_name": "测试庄园",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        }
    )
    assert form.is_valid() is False
    assert "email" in form.errors


@pytest.mark.django_db
def test_signup_form_requires_manor_name():
    form = SignUpForm(
        data={
            "username": "no_manor_name_user",
            "email": "no_manor_name@example.com",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        }
    )
    assert form.is_valid() is False
    assert "manor_name" in form.errors


@pytest.mark.django_db
def test_register_view_handles_integrity_error_duplicate_email_race(client, monkeypatch):
    User.objects.create_user(username="race_existing", email="race@example.com", password="pass123")

    monkeypatch.setattr("accounts.forms.SignUpForm.clean_email", lambda self: self.cleaned_data["email"])
    monkeypatch.setattr("accounts.forms.SignUpForm.validate_unique", lambda self: None)

    response = client.post(
        reverse("accounts:register"),
        {
            "username": "race_new_user",
            "email": "race@example.com",
            "manor_name": "竞速庄园A",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        },
    )
    assert response.status_code == 200
    assert User.objects.filter(username="race_new_user").exists() is False
    form = response.context["form"]
    assert "email" in form.errors
    assert any("该邮箱已注册" in msg for msg in form.errors["email"])


@pytest.mark.django_db
def test_register_view_handles_integrity_error_duplicate_username_race(client, monkeypatch):
    User.objects.create_user(username="race_same_name", email="race_name_old@example.com", password="pass123")

    monkeypatch.setattr("accounts.forms.SignUpForm.clean_email", lambda self: self.cleaned_data["email"])
    monkeypatch.setattr("accounts.forms.SignUpForm.clean_username", lambda self: self.cleaned_data["username"])
    monkeypatch.setattr("accounts.forms.SignUpForm.validate_unique", lambda self: None)

    response = client.post(
        reverse("accounts:register"),
        {
            "username": "race_same_name",
            "email": "race_name_new@example.com",
            "manor_name": "竞速庄园B",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        },
    )
    assert response.status_code == 200
    assert User.objects.filter(email="race_name_new@example.com").exists() is False
    form = response.context["form"]
    assert "username" in form.errors
    assert any("该用户名已存在" in msg for msg in form.errors["username"])


@pytest.mark.django_db
def test_register_view_handles_integrity_error_duplicate_manor_name_race(client, monkeypatch):
    owner = User(username="race_manor_owner")
    owner.set_password("pass123")
    User.objects.bulk_create([owner])
    owner = User.objects.get(username="race_manor_owner")
    ensure_manor(owner, initial_name="竞速庄园C")

    monkeypatch.setattr("accounts.forms.SignUpForm.clean_manor_name", lambda self: self.cleaned_data["manor_name"])

    response = client.post(
        reverse("accounts:register"),
        {
            "username": "race_new_manor_user",
            "email": "race_new_manor_user@example.com",
            "manor_name": "竞速庄园C",
            "region": "overseas",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        },
    )
    assert response.status_code == 200
    assert User.objects.filter(username="race_new_manor_user").exists() is False
    form = response.context["form"]
    assert "manor_name" in form.errors
    assert any("该庄园名称已被使用" in msg for msg in form.errors["manor_name"])


@pytest.mark.django_db
def test_register_page_uses_concise_hero_copy(client):
    response = client.get(reverse("accounts:register"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "建立你的庄园势力" in content
    assert "完成注册后将自动登录，开启你的春秋霸业吧~" in content
    assert "新玩家招募" not in content
    assert "初始赠送基础资源" not in content
    assert "地区将影响地图首页默认显示范围" not in content
    assert "后续可通过成长与策略拓展势力" not in content


def _build_login_request(remote_addr: str = "127.0.0.1"):
    request = RequestFactory().post("/accounts/login/", data={"username": "u", "password": "x"})
    request.META["REMOTE_ADDR"] = remote_addr
    return request


@pytest.mark.django_db
def test_login_attempts_lock_after_limit(monkeypatch):
    cache.clear()
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_LIMIT", 3)
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_WINDOW", 60)
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 120)

    request = _build_login_request()

    assert account_views._record_failed_attempt(request, "tester") == 1
    assert account_views._check_login_attempts(request, "tester")[0] is False
    assert account_views._record_failed_attempt(request, "tester") == 2
    assert account_views._check_login_attempts(request, "tester")[0] is False

    assert account_views._record_failed_attempt(request, "tester") == 3
    is_locked, _remaining = account_views._check_login_attempts(request, "tester")
    assert is_locked is True


@pytest.mark.django_db
def test_clear_login_attempts_removes_lock(monkeypatch):
    cache.clear()
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_LIMIT", 2)
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_WINDOW", 60)
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 120)

    request = _build_login_request(remote_addr="10.0.0.2")
    account_views._record_failed_attempt(request, "tester2")
    account_views._record_failed_attempt(request, "tester2")
    assert account_views._check_login_attempts(request, "tester2")[0] is True

    account_views._clear_login_attempts(request, "tester2")
    assert account_views._check_login_attempts(request, "tester2")[0] is False


@pytest.mark.django_db
def test_clear_login_attempts_can_preserve_ip_bucket(monkeypatch):
    cache.clear()
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_LIMIT", 2)
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_WINDOW", 60)
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 120)

    request = _build_login_request(remote_addr="10.0.0.22")
    account_views._record_failed_attempt(request, "tester_ip_preserved")
    account_views._record_failed_attempt(request, "tester_ip_preserved")

    ip_key, username_key = account_views._get_login_attempt_key(request, "tester_ip_preserved")
    ip_lock_key, username_lock_key = account_views._get_login_lock_key(request, "tester_ip_preserved")

    account_views._clear_login_attempts(request, "tester_ip_preserved", clear_ip=False)

    assert cache.get(username_key) is None
    assert cache.get(username_lock_key) is None
    assert cache.get(ip_key) is not None
    assert cache.get(ip_lock_key) is not None
    assert account_views._check_login_attempts(request, "another_user")[0] is True


@pytest.mark.django_db
def test_login_view_clears_ip_bucket_after_success(client, monkeypatch):
    cache.clear()
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_LIMIT", 3)
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_WINDOW", 60)
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 120)

    user = User.objects.create_user(username="login_success_user", password="StrongPass123!")
    request = _build_login_request(remote_addr="10.0.0.30")
    ip_key, _username_key = account_views._get_login_attempt_key(request, user.username)
    ip_lock_key, _username_lock_key = account_views._get_login_lock_key(request, user.username)

    assert account_views._record_failed_attempt(request, user.username) == 1
    assert cache.get(ip_key) == 1

    response = client.post(
        reverse("accounts:login"),
        {"username": user.username, "password": "StrongPass123!"},
        REMOTE_ADDR="10.0.0.30",
    )

    assert response.status_code == 302
    assert cache.get(ip_key) is None
    assert cache.get(ip_lock_key) is None
    assert account_views._check_login_attempts(request, "another_user")[0] is False


@pytest.mark.django_db
def test_check_login_attempts_respects_username_lock(monkeypatch):
    request = _build_login_request(remote_addr="10.0.0.8")
    _ip_lock_key, username_lock_key = account_views._get_login_lock_key(request, "locked_user")

    def fake_cache_get(key, default=None):
        if key == username_lock_key:
            return 1
        return default

    monkeypatch.setattr(account_views.cache, "get", fake_cache_get)
    monkeypatch.setattr(account_views.cache, "ttl", lambda _key: 60, raising=False)

    assert account_views._check_login_attempts(request, "locked_user")[0] is True


@pytest.mark.django_db
def test_check_login_attempts_fallback_ttl_when_cache_ttl_invalid(monkeypatch):
    request = _build_login_request(remote_addr="10.0.0.9")
    ip_lock_key, _username_lock_key = account_views._get_login_lock_key(request, "locked_user_2")

    def fake_cache_get(key, default=None):
        if key == ip_lock_key:
            return 1
        return default

    monkeypatch.setattr(account_views.cache, "get", fake_cache_get)
    monkeypatch.setattr(account_views.cache, "ttl", lambda _key: -1, raising=False)
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 123)

    is_locked, ttl = account_views._check_login_attempts(request, "locked_user_2")
    assert is_locked is True
    assert ttl == 123


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_increment_attempt_counter_fail_closed_on_incr_error_in_production(monkeypatch):
    """生产环境下 cache.incr 失败时 fail-closed，返回 LOGIN_ATTEMPT_LIMIT 触发锁定。"""
    key = "login_attempts:test-corrupt"

    monkeypatch.setattr(account_views.cache, "add", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        account_views.cache, "incr", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("no incr"))
    )

    attempts = account_views._increment_attempt_counter(key)
    assert attempts == account_views.LOGIN_ATTEMPT_LIMIT


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_increment_attempt_counter_debug_fallback_resets_on_non_numeric_cache_value(monkeypatch):
    """DEBUG 模式下 cache.incr 失败 + 非数字缓存值时，本地 fallback 重置为 1。"""
    key = "login_attempts:test-corrupt-debug"
    set_mock = Mock()

    monkeypatch.setattr(account_views.cache, "add", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        account_views.cache, "incr", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("no incr"))
    )
    monkeypatch.setattr(account_views.cache, "get", lambda *_args, **_kwargs: "not-a-number")
    monkeypatch.setattr(account_views.cache, "set", set_mock)

    attempts = account_views._increment_attempt_counter(key)
    assert attempts == 1
    set_mock.assert_called_once()
    assert set_mock.call_args[0][1] == 1


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_increment_attempt_counter_fail_closed_when_cache_add_raises_in_production(monkeypatch):
    """生产环境下 cache.add 抛异常（added=None）时 fail-closed，返回 LOGIN_ATTEMPT_LIMIT。"""
    key = "login_attempts:test-cache-error"

    monkeypatch.setattr(
        account_views.cache, "add", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("add fail"))
    )

    attempts = account_views._increment_attempt_counter(key)
    assert attempts == account_views.LOGIN_ATTEMPT_LIMIT


@pytest.mark.django_db
def test_check_login_attempts_uses_local_lock_when_cache_get_errors(monkeypatch):
    request = _build_login_request(remote_addr="10.0.0.10")
    ip_lock_key, _username_lock_key = account_views._get_login_lock_key(request, "tester")
    account_views._LOCAL_LOGIN_CACHE.clear()
    account_views._local_login_cache_set(ip_lock_key, 1, timeout=account_views.LOGIN_LOCKOUT_DURATION)

    monkeypatch.setattr(
        account_views.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("cache down")),
    )

    is_locked, ttl = account_views._check_login_attempts(request, "tester")
    assert is_locked is True
    assert ttl == account_views.LOGIN_LOCKOUT_DURATION


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_record_failed_attempt_fail_closed_when_cache_ops_fail_in_production(monkeypatch):
    """生产环境下 cache.add 抛异常时 fail-closed：首次尝试即返回 LOGIN_ATTEMPT_LIMIT 并触发锁定。"""
    request = _build_login_request(remote_addr="10.0.0.13")
    account_views._LOCAL_LOGIN_CACHE.clear()
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_LIMIT", 2)
    monkeypatch.setattr(account_views, "LOGIN_ATTEMPT_WINDOW", 60)
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 120)
    monkeypatch.setattr(
        account_views.cache,
        "add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("cache add down")),
    )

    # 首次 cache.add 失败 → fail-closed → 返回 LOGIN_ATTEMPT_LIMIT(2) → 触发锁定
    result = account_views._record_failed_attempt(request, "tester_fail_closed")
    assert result == account_views.LOGIN_ATTEMPT_LIMIT
    assert account_views._check_login_attempts(request, "tester_fail_closed")[0] is True


@pytest.mark.django_db
def test_check_login_attempts_fallback_ttl_when_cache_ttl_errors(monkeypatch):
    request = _build_login_request(remote_addr="10.0.0.11")
    ip_lock_key, _username_lock_key = account_views._get_login_lock_key(request, "locked_user_3")

    def fake_cache_get(key, default=None):
        if key == ip_lock_key:
            return 1
        return default

    monkeypatch.setattr(account_views.cache, "get", fake_cache_get)
    monkeypatch.setattr(
        account_views.cache,
        "ttl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("ttl fail")),
        raising=False,
    )
    monkeypatch.setattr(account_views, "LOGIN_LOCKOUT_DURATION", 321)

    is_locked, ttl = account_views._check_login_attempts(request, "locked_user_3")
    assert is_locked is True
    assert ttl == 321


@pytest.mark.django_db
def test_clear_login_attempts_tolerates_cache_delete_errors(monkeypatch):
    request = _build_login_request(remote_addr="10.0.0.12")
    delete_mock = Mock(side_effect=ConnectionError("delete fail"))
    monkeypatch.setattr(account_views.cache, "delete", delete_mock)
    account_views._LOCAL_LOGIN_CACHE.clear()
    account_views._local_login_cache_set("login_attempts:ip:10.0.0.12", 2, timeout=60)

    account_views._clear_login_attempts(request, "tester")
    assert delete_mock.call_count >= 2
    assert not account_views._LOCAL_LOGIN_CACHE


@pytest.mark.django_db
def test_check_login_attempts_runtime_marker_cache_get_bubbles_up(monkeypatch):
    request = _build_login_request(remote_addr="10.0.0.14")

    monkeypatch.setattr(
        account_views.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        account_views._check_login_attempts(request, "tester_runtime_marker")


@pytest.mark.django_db
def test_increment_attempt_counter_runtime_marker_cache_add_bubbles_up(monkeypatch):
    key = "login_attempts:test-runtime-marker"

    monkeypatch.setattr(
        account_views.cache, "add", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cache add down"))
    )

    with pytest.raises(RuntimeError, match="cache add down"):
        account_views._increment_attempt_counter(key)
