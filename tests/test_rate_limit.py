from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from redis.exceptions import RedisError

from core.utils.rate_limit import rate_limit_redirect

User = get_user_model()


def _attach_session_and_messages(request):
    SessionMiddleware(lambda req: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


@rate_limit_redirect("test_rate_limit", limit=1, window_seconds=60, redirect_url="/rate-limit")
def _limited_view(request):
    return HttpResponse("ok")


class RateLimitRedirectTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="tester", password="pass")

    def test_rate_limit_redirect_triggers(self):
        request = self.factory.post("/test")
        request.user = self.user
        _attach_session_and_messages(request)

        response = _limited_view(request)
        self.assertEqual(response.status_code, 200)

        response = _limited_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/rate-limit")

    def test_rate_limit_redirect_returns_busy_when_cache_down(self):
        request = self.factory.post("/test")
        request.user = self.user
        _attach_session_and_messages(request)

        original_add = cache.add

        def raise_redis_error(*args, **kwargs):
            raise RedisError("cache unavailable")

        try:
            cache.add = raise_redis_error
            response = _limited_view(request)
        finally:
            cache.add = original_add

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/rate-limit")
        self.assertEqual([str(message) for message in get_messages(request)], ["系统繁忙，请稍后再试"])

    def test_rate_limit_redirect_blank_key_func_falls_back(self):
        @rate_limit_redirect(
            "test_rate_limit_blank_key",
            limit=1,
            window_seconds=60,
            redirect_url="/rate-limit",
            key_func=lambda _req: "   ",
        )
        def limited_view(request):
            return HttpResponse("ok")

        request = self.factory.post("/test")
        request.user = self.user
        _attach_session_and_messages(request)

        first = limited_view(request)
        second = limited_view(request)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second["Location"], "/rate-limit")

    def test_rate_limit_redirect_key_func_error_bubbles_up(self):
        def bad_key_func(_request):
            raise RuntimeError("boom")

        @rate_limit_redirect(
            "test_rate_limit_bad_key",
            limit=1,
            window_seconds=60,
            redirect_url="/rate-limit",
            key_func=bad_key_func,
        )
        def limited_view(request):
            return HttpResponse("ok")

        request = self.factory.post("/test")
        request.user = self.user
        _attach_session_and_messages(request)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            limited_view(request)

    def test_rate_limit_redirect_ajax_returns_json_when_limited(self):
        request = self.factory.post(
            "/test",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        request.user = self.user
        _attach_session_and_messages(request)

        first = _limited_view(request)
        second = _limited_view(request)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        payload = json.loads(second.content.decode("utf-8"))
        self.assertEqual(payload["success"], False)
        self.assertIn("频繁", payload["error"])

    def test_rate_limit_redirect_ajax_returns_json_when_cache_down(self):
        request = self.factory.post(
            "/test",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        request.user = self.user
        _attach_session_and_messages(request)

        original_add = cache.add

        def raise_redis_error(*args, **kwargs):
            raise RedisError("cache unavailable")

        try:
            cache.add = raise_redis_error
            response = _limited_view(request)
        finally:
            cache.add = original_add

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(payload["success"], False)
        self.assertIn("繁忙", payload["error"])

    def test_rate_limit_redirect_programming_error_bubbles_up(self):
        request = self.factory.post("/test")
        request.user = self.user
        _attach_session_and_messages(request)

        original_add = cache.add

        def raise_programming_error(*args, **kwargs):
            raise AssertionError("broken cache contract")

        try:
            cache.add = raise_programming_error
            with self.assertRaisesRegex(AssertionError, "broken cache contract"):
                _limited_view(request)
        finally:
            cache.add = original_add


def test_rate_limit_redirect_rejects_non_positive_limit():
    with pytest.raises(AssertionError, match="positive limit"):
        rate_limit_redirect("bad", limit=0, window_seconds=60)


def test_rate_limit_redirect_rejects_non_positive_window():
    with pytest.raises(AssertionError, match="positive window_seconds"):
        rate_limit_redirect("bad", limit=1, window_seconds=0)
