import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from accounts import views as account_views


@pytest.fixture(autouse=True)
def isolated_login_cache():
    cache.clear()
    account_views._LOCAL_LOGIN_CACHE.clear()
    yield
    cache.clear()
    account_views._LOCAL_LOGIN_CACHE.clear()


def login_request(ip="203.0.113.50"):
    return RequestFactory().post(reverse("accounts:login"), REMOTE_ADDR=ip)


@pytest.mark.parametrize("release", ["expire", "delete"])
def test_released_shared_lock_does_not_survive_in_local_cache(release, monkeypatch):
    request = login_request()
    lock_keys = account_views._get_login_lock_key(request, "expired_user")
    for key in lock_keys:
        account_views._safe_cache_set(key, 1, timeout=60)

    # 登录页读取过旧锁后，共享缓存到期或被另一进程清理。
    assert account_views._check_login_attempts(request, "expired_user")[0] is True
    for key in lock_keys:
        if release == "expire":
            cache.set(key, 1, timeout=0)
        else:
            cache.delete(key)

    assert account_views._check_login_attempts(request, "expired_user") == (False, 0)
    assert all(account_views._local_login_cache_get(key) is None for key in lock_keys)

    # 已确认失效的锁不能在后续缓存故障时复活。
    def unavailable(*args, **kwargs):
        raise ConnectionError("cache unavailable")

    monkeypatch.setattr(account_views.cache, "get", unavailable)
    assert account_views._check_login_attempts(request, "expired_user") == (False, 0)


@pytest.mark.django_db
def test_shared_mobile_ip_can_login_after_another_account_is_locked(client, django_user_model):
    user = django_user_model.objects.create_user(username="mobile_user", password="StrongPass123!")
    request = login_request()
    for _ in range(account_views.LOGIN_ATTEMPT_LIMIT):
        account_views._record_failed_attempt(request, "another_user")

    assert account_views._check_login_attempts(request, "another_user")[0] is True
    response = client.post(
        reverse("accounts:login"),
        {"username": user.username, "password": "StrongPass123!"},
        REMOTE_ADDR=request.META["REMOTE_ADDR"],
        HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
    )
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


def test_account_lock_still_applies_across_networks():
    for index in range(account_views.LOGIN_ATTEMPT_LIMIT):
        account_views._record_failed_attempt(login_request(f"203.0.113.{index + 1}"), "same_user")

    assert account_views._check_login_attempts(login_request("198.51.100.1"), "same_user")[0] is True


def test_shared_ip_still_locks_at_its_own_limit():
    request = login_request()
    for index in range(account_views.LOGIN_IP_ATTEMPT_LIMIT - 1):
        account_views._record_failed_attempt(request, f"different_user_{index}")
    assert account_views._check_login_attempts(request, "fresh_user")[0] is False

    account_views._record_failed_attempt(request, "last_user")
    assert account_views._check_login_attempts(request, "fresh_user")[0] is True


@override_settings(DEBUG=False)
def test_shared_ip_still_fails_closed_when_counter_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("cache unavailable")

    monkeypatch.setattr(account_views.cache, "add", unavailable)
    request = login_request()
    account_views._record_failed_attempt(request, "failed_user")
    assert account_views._check_login_attempts(request, "fresh_user")[0] is True


@pytest.mark.django_db
def test_failed_login_message_uses_account_limit_on_shared_ip(client):
    request = login_request()
    for index in range(account_views.LOGIN_ATTEMPT_LIMIT):
        account_views._record_failed_attempt(request, f"other_user_{index}")

    response = client.post(
        reverse("accounts:login"),
        {"username": "new_user", "password": "wrong-password"},
        REMOTE_ADDR=request.META["REMOTE_ADDR"],
    )
    assert response.status_code == 200
    assert "用户名或密码错误，请重试" in response.content.decode()
    assert "登录尝试次数过多" not in response.content.decode()


@pytest.mark.parametrize("failure", [ConnectionError, RedisConnectionError, RedisTimeoutError, None])
def test_unsynced_lock_survives_shared_cache_miss_until_local_expiry(monkeypatch, failure):
    now = [100.0]
    monkeypatch.setattr(account_views.time, "monotonic", lambda: now[0])

    def failed_write(*args, **kwargs):
        if failure is not None:
            raise failure("write unavailable")
        return False

    monkeypatch.setattr(account_views.cache, "set", failed_write)
    request = login_request()
    ip_lock, _ = account_views._get_login_lock_key(request)
    account_views._safe_cache_set(ip_lock, 1, timeout=10)

    now[0] = 109.0
    assert account_views._check_login_attempts(request)[0] is True
    now[0] = 111.0
    assert account_views._check_login_attempts(request)[0] is False


def test_recovered_shared_lock_clears_unsynced_marker(monkeypatch):
    request = login_request()
    ip_lock, _ = account_views._get_login_lock_key(request)
    account_views._local_login_cache_set(ip_lock, 1, timeout=60, unsynced=True)
    cache.set(ip_lock, 1, timeout=60)

    assert account_views._check_login_attempts(request)[0] is True
    cache.delete(ip_lock)
    assert account_views._check_login_attempts(request)[0] is False


def test_reading_shared_lock_does_not_extend_local_fallback(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(account_views.time, "monotonic", lambda: now[0])
    request = login_request()
    ip_lock, _ = account_views._get_login_lock_key(request)
    account_views._safe_cache_set(ip_lock, 1, timeout=10)

    now[0] = 109.0
    assert account_views._check_login_attempts(request)[0] is True

    def unavailable(*args, **kwargs):
        raise ConnectionError("read unavailable")

    monkeypatch.setattr(account_views.cache, "get", unavailable)
    now[0] = 111.0
    assert account_views._check_login_attempts(request)[0] is False


@pytest.mark.parametrize("first_attempt", [True, False])
def test_attempt_counter_does_not_overwrite_concurrent_increment(monkeypatch, first_attempt):
    key = "login_attempts:concurrent"
    if first_attempt:
        original_add = cache.add

        def interleaved_add(*args, **kwargs):
            result = original_add(*args, **kwargs)
            cache.incr(key)
            return result

        monkeypatch.setattr(account_views.cache, "add", interleaved_add)
        expected_result = 1
    else:
        cache.set(key, 1, timeout=60)
        original_incr = cache.incr

        def interleaved_incr(*args, **kwargs):
            result = original_incr(*args, **kwargs)
            original_incr(key)
            return result

        monkeypatch.setattr(account_views.cache, "incr", interleaved_incr)
        expected_result = 2

    assert account_views._increment_attempt_counter(key) == expected_result
    assert cache.get(key) == expected_result + 1


@override_settings(DEBUG=False)
@pytest.mark.parametrize("exception", [ConnectionError, RedisConnectionError, RedisTimeoutError, RuntimeError])
def test_counter_touch_handles_infrastructure_errors_only(monkeypatch, exception):
    key = "login_attempts:touch_failure"
    cache.set(key, 1, timeout=60)

    def unavailable(*args, **kwargs):
        raise exception("touch unavailable")

    monkeypatch.setattr(account_views.cache, "touch", unavailable)
    if exception is not RuntimeError:
        assert account_views._increment_attempt_counter(key) == account_views.LOGIN_ATTEMPT_LIMIT
    else:
        with pytest.raises(RuntimeError, match="touch unavailable"):
            account_views._increment_attempt_counter(key)
